//! The [`Panel`]: the driver loop between the headless core and a [`Backend`].
//!
//! A `Panel` owns everything the core needs to produce frames — the
//! heterogeneous widget list, the [`widgets::DataSource`] the poll-based
//! widgets read, and the [`widgets::LayoutEngine`] built from the
//! [`widgets::BarConfig`] — plus the current bar width (cells) and the poll
//! interval. The backend is *injected* through the [`Backend`] trait on every
//! call, so the same driver runs headlessly against
//! [`crate::headless::HeadlessBackend`] and, later, against the real
//! Wayland `layer-shell` backend (a follow-up task) with no driver changes.
//!
//! # The loop
//!
//! [`Panel::tick`] is one poll-and-paint: update every widget against the
//! source, lay the renders out on the current width, and paint the resulting
//! [`Frame`]. [`Panel::run`] is the whole session:
//!
//! ```text
//! open() ──▶ ┌ tick()  (paint one frame)
//!            │ wait(poll_interval) ──▶ Tick    → loop
//!            │ wait(poll_interval) ──▶ Resize  → loop (new width, next tick)
//!            └ wait(poll_interval) ──▶ Quit    → close(), done
//! ```
//!
//! The width comes from the backend (`open`, `Resize`) — the panel never
//! guesses it — and a resize takes effect on the *next* tick.

use std::time::Duration;

use widgets::{BarConfig, DataSource, LayoutConfig, LayoutEngine, Widget, WidgetRender};

use crate::backend::{Backend, BackendError, BackendEvent, Frame, PaintedWidget, SECTIONS};

/// What a finished [`Panel::run`] reports.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RunReport {
    /// How many frames were painted (one per loop iteration).
    pub frames: u64,
    /// The bar width (cells) in effect when the run ended.
    pub width: u32,
}

/// The panel driver: widgets + data source + layout engine + event loop.
///
/// Construct one per session with [`Panel::new`]; it consumes itself in
/// [`Panel::run`]. For a single poll-and-paint without a session, use
/// [`Panel::tick`] on a width set with [`Panel::with_width`].
pub struct Panel {
    widgets: Vec<Box<dyn Widget>>,
    source: Box<dyn DataSource>,
    layout: LayoutEngine,
    poll_interval: Duration,
    width: u32,
}

impl Panel {
    /// Build a panel from a widget list, a data source, and bar settings.
    ///
    /// The `LayoutEngine` is built from `bar.gap_cells` and the poll loop uses
    /// `bar.poll_interval_ms`, so the config model and the loop never drift.
    /// The bar width is unknown until a backend says otherwise
    /// ([`Panel::with_width`], or `Backend::open` inside [`Panel::run`]).
    pub fn new(
        widgets: Vec<Box<dyn Widget>>,
        source: Box<dyn DataSource>,
        bar: BarConfig,
    ) -> Self {
        Panel {
            widgets,
            source,
            layout: LayoutEngine::new(LayoutConfig {
                gap_cells: bar.gap_cells,
            }),
            poll_interval: Duration::from_millis(bar.poll_interval_ms),
            width: 0,
        }
    }

    /// The current bar width in text cells.
    pub fn width(&self) -> u32 {
        self.width
    }

    /// Set the bar width directly (tests, and `--print-layout`-style one-shot
    /// runs where no backend negotiates a size).
    pub fn with_width(mut self, width_cells: u32) -> Self {
        self.width = width_cells;
        self
    }

    /// Set the bar width in place (a backend's `open`/`Resize`, and the
    /// bounded steady-state run in [`crate::live_rss`] drive the layout
    /// width this way without rebuilding the panel).
    pub fn set_width(&mut self, width_cells: u32) {
        self.width = width_cells;
    }

    /// The poll interval configured for this panel.
    pub fn poll_interval(&self) -> Duration {
        self.poll_interval
    }

    /// How many widgets the panel holds.
    pub fn widget_count(&self) -> usize {
        self.widgets.len()
    }

    /// Typed access to one widget by id: the **state-fed channel**.
    ///
    /// A `Vec<Box<dyn Widget>>` can tick every widget but cannot call a
    /// concrete setter like `Workspaces::set_state` or `Template::set_vars`.
    /// The backend drives state-fed widgets through this downcast: it keeps
    /// the widget's id (from the config) and its concrete type (which it
    /// constructed), and reaches the *same instance* the panel ticks.
    ///
    /// Returns `None` when no widget has that id, or when it is not of type
    /// `T` — a config/type mismatch is a `None`, never a panic.
    pub fn widget_mut<T: Widget + 'static>(&mut self, id: &str) -> Option<&mut T> {
        let widget = self.widgets.iter_mut().find(|w| w.id() == id)?;
        widget.as_any_mut().downcast_mut::<T>()
    }

    /// One poll-and-paint: tick every widget against the source, lay the
    /// renders out on the current width, paint the frame to `backend`, and
    /// return it (so callers can inspect or golden-file exactly what was
    /// painted).
    pub fn tick(&mut self, backend: &mut impl Backend) -> Frame {
        // 1. Poll: every widget produces its current render.
        let renders: Vec<WidgetRender> = self
            .widgets
            .iter_mut()
            .map(|w| w.update(&*self.source))
            .collect();

        // 2. Layout: pure geometry on the current width.
        let layout = self.layout.layout(self.width, &renders);

        // 3. Paint list: sections in display order, widgets in their
        //    per-section (i.e. x) order. Sections never overlap, so this is
        //    the full bar's display order.
        let mut placed: Vec<PaintedWidget> = Vec::with_capacity(layout.left.len()
            + layout.center.len()
            + layout.right.len());
        for section in SECTIONS {
            for pw in layout.section(*section) {
                let render = &renders[pw.index];
                placed.push(PaintedWidget {
                    x: pw.x,
                    width: pw.width,
                    runs: render.runs().to_vec(),
                });
            }
        }

        let frame = Frame {
            width: self.width,
            placed,
        };
        backend.paint(&frame);
        frame
    }

    /// Run the panel to completion on `backend`: open, then loop
    /// tick → wait until a `Quit` event, and close.
    ///
    /// `open`'s width and any `Resize` events drive the layout; a resize takes
    /// effect on the next tick. Returns a [`RunReport`] on a clean `Quit`, or
    /// the [`BackendError`] if `open` failed (in which case nothing was
    /// painted and `close` is not called — there is nothing to release).
    pub fn run(mut self, backend: &mut impl Backend) -> Result<RunReport, BackendError> {
        self.width = backend.open()?;
        let mut frames = 0u64;
        loop {
            self.tick(backend);
            frames += 1;
            match backend.wait(self.poll_interval) {
                BackendEvent::Tick => {}
                BackendEvent::Resize { width_cells } => self.width = width_cells,
                BackendEvent::Quit => break,
            }
        }
        backend.close();
        Ok(RunReport {
            frames,
            width: self.width,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::BackendEvent;
    use crate::headless::HeadlessBackend;
    use std::collections::BTreeMap;
    use widgets::{Section, Text, Widget, WorkspaceState, Workspaces};

    /// The binary-crate test double for `widgets::DataSource` (replicated here
    /// on purpose: the core's `MockSource` is `#[cfg(test)]` in the core crate
    /// and does not ship).
    ///
    /// The readings live in an `Arc<Mutex<_>>` so a test can keep a second
    /// handle and advance the "world" between ticks of the same panel.
    #[derive(Default)]
    struct TsrcInner {
        now: Option<i64>,
        tz: Option<i32>,
        sys: BTreeMap<String, String>,
    }

    #[derive(Clone)]
    struct Tsrc(std::sync::Arc<std::sync::Mutex<TsrcInner>>);

    impl Default for Tsrc {
        fn default() -> Self {
            Tsrc(std::sync::Arc::new(std::sync::Mutex::new(TsrcInner::default())))
        }
    }

    impl Tsrc {
        /// A second handle onto the *same* readings (the panel keeps one, the
        /// test mutates the world through this one).
        fn shared(&self) -> Self {
            Tsrc(self.0.clone())
        }
    }

    impl DataSource for Tsrc {
        fn now_secs(&self) -> Option<i64> {
            self.0.lock().unwrap().now
        }
        fn tz_offset_secs(&self) -> Option<i32> {
            self.0.lock().unwrap().tz
        }
        fn power_supply(&self, entry: &str) -> Option<String> {
            self.0.lock().unwrap().sys.get(entry).cloned()
        }
    }

    /// A poll-based test widget: renders the raw `now_secs` or `??` — proves
    /// tick() actually pulls data through the source on every poll.
    struct NowWidget {
        section: Section,
    }

    impl Widget for NowWidget {
        fn id(&self) -> &str {
            "now"
        }
        fn section(&self) -> Section {
            self.section
        }
        fn update(&mut self, source: &dyn DataSource) -> WidgetRender {
            let text = source
                .now_secs()
                .map(|n| n.to_string())
                .unwrap_or_else(|| "?".to_string());
            WidgetRender::new(self.section, vec![widgets::TextRun::new(text, widgets::Style::Normal)])
        }

        fn as_any(&self) -> &dyn std::any::Any {
            self
        }

        fn as_any_mut(&mut self) -> &mut dyn std::any::Any {
            self
        }
    }

    fn bar(gap: u32, poll_ms: u64) -> BarConfig {
        BarConfig {
            gap_cells: gap,
            poll_interval_ms: poll_ms,
        }
    }

    #[test]
    fn panel_starts_at_zero_width_until_told() {
        let panel = Panel::new(Vec::new(), Box::new(Tsrc::default()), bar(1, 1000));
        assert_eq!(panel.width(), 0);
        assert_eq!(panel.widget_count(), 0);
        assert_eq!(panel.poll_interval(), Duration::from_millis(1000));
    }

    #[test]
    fn with_width_sets_the_bar_width() {
        let panel = Panel::new(Vec::new(), Box::new(Tsrc::default()), bar(1, 1000)).with_width(48);
        assert_eq!(panel.width(), 48);
    }

    /// `set_width` mutates the width in place (no rebuild), so a live backend's
    /// `open`/`Resize` can drive the layout width on the same panel.
    #[test]
    fn set_width_updates_the_width_in_place() {
        let mut panel = Panel::new(Vec::new(), Box::new(Tsrc::default()), bar(1, 1000));
        assert_eq!(panel.width(), 0);
        panel.set_width(42);
        assert_eq!(panel.width(), 42);
        panel.set_width(0); // a reflow back to zero is legal
        assert_eq!(panel.width(), 0);
        // The widgets are untouched by a width change.
        assert_eq!(panel.widget_count(), 0);
    }

    /// Hand-computed: width 10, gap 1; Left "ab" → x=0, Right "ef" → x=8,
    /// Center "cd" centered in [2,8) → x = 2 + (6-2)/2 = 4.
    /// Frame: `ab  cd  ef`.
    #[test]
    fn tick_lays_out_all_three_sections() {
        let mut panel = Panel::new(
            vec![
                Box::new(Text::new(Section::Left, "ab")),
                Box::new(Text::new(Section::Center, "cd")),
                Box::new(Text::new(Section::Right, "ef")),
            ],
            Box::new(Tsrc::default()),
            bar(1, 1000),
        )
        .with_width(10);
        let mut backend = HeadlessBackend::new(10);
        let frame = panel.tick(&mut backend);

        assert_eq!(frame.width, 10);
        assert_eq!(frame.placed.len(), 3);
        assert_eq!(frame.placed[0].x, 0); // left first, in x order
        assert_eq!(frame.placed[1].x, 4); // center
        assert_eq!(frame.placed[2].x, 8); // right
        assert_eq!(crate::headless::render_frame(&frame), "ab  cd  ef");
        // The backend recorded exactly the frame the panel returns.
        assert_eq!(backend.paints(), std::slice::from_ref(&frame));
    }

    /// Hand-computed: width 5, gap 1; "ab"(2) + "cd"(2) + "ef"(2) + 2 gaps =
    /// 8 > 5, so the outermost left widget is dropped first (tie → left),
    /// leaving 5 ≤ 5. Right "ef" → x=3; center "cd" in [0,3) → x=0.
    /// Frame: `cd ef`.
    #[test]
    fn tick_drops_overflowing_widgets_whole_and_outside_in() {
        let mut panel = Panel::new(
            vec![
                Box::new(Text::new(Section::Left, "ab")),
                Box::new(Text::new(Section::Center, "cd")),
                Box::new(Text::new(Section::Right, "ef")),
            ],
            Box::new(Tsrc::default()),
            bar(1, 1000),
        )
        .with_width(5);
        let mut backend = HeadlessBackend::new(5);
        let frame = panel.tick(&mut backend);

        assert_eq!(frame.placed.len(), 2);
        assert_eq!(frame.placed[0].x, 0); // "cd"
        assert_eq!(frame.placed[0].runs[0].text, "cd");
        assert_eq!(frame.placed[1].x, 3); // "ef"
        assert_eq!(frame.placed[1].runs[0].text, "ef");
        assert_eq!(crate::headless::render_frame(&frame), "cd ef");
    }

    /// Zero-width widgets draw nothing and reserve no gap: the non-empty
    /// widgets still center as if the empty one were not there.
    #[test]
    fn tick_excludes_zero_width_widgets() {
        let mut panel = Panel::new(
            vec![
                Box::new(Text::new(Section::Left, "ab")),
                Box::new(Text::new(Section::Center, "")), // zero-width
                Box::new(Text::new(Section::Right, "cd")),
            ],
            Box::new(Tsrc::default()),
            bar(1, 1000),
        )
        .with_width(10);
        let mut backend = HeadlessBackend::new(10);
        let frame = panel.tick(&mut backend);

        // Hand-computed: "ab" x=0, "cd" x=8.
        assert_eq!(frame.placed.len(), 2);
        assert_eq!(frame.placed[0].x, 0);
        assert_eq!(frame.placed[1].x, 8);
        assert_eq!(crate::headless::render_frame(&frame), "ab      cd");
    }

    /// Poll-based widgets re-read the source on every tick: the same panel
    /// renders different frames as the source moves.
    #[test]
    fn tick_polls_the_source_on_every_call() {
        let src = Tsrc(std::sync::Arc::new(std::sync::Mutex::new(TsrcInner {
            now: Some(1_000),
            ..Default::default()
        })));
        let world = src.shared();
        let mut panel = Panel::new(
            vec![Box::new(NowWidget {
                section: Section::Right,
            })],
            Box::new(src),
            bar(1, 1000),
        )
        .with_width(10);

        let mut backend = HeadlessBackend::new(10);
        let f1 = panel.tick(&mut backend);
        assert_eq!(f1.placed[0].runs[0].text, "1000");
        assert_eq!(crate::headless::render_frame(&f1), "      1000");

        // The world advances; the next tick must reflect it.
        world.0.lock().unwrap().now = Some(1_001);
        let f2 = panel.tick(&mut backend);
        assert_eq!(f2.placed[0].runs[0].text, "1001");

        // And when the reading vanishes the widget falls back.
        world.0.lock().unwrap().now = None;
        let f3 = panel.tick(&mut backend);
        assert_eq!(f3.placed[0].runs[0].text, "?");
        assert_eq!(backend.frames.len(), 3);
    }

    /// State-fed widgets render whatever state was last pushed through the
    /// concrete type — the `dyn` list only ticks, it never sets state. The
    /// backend drives state through [`Panel::widget_mut`] (the downcast
    /// channel), reaching the *same instance* the panel ticks.
    #[test]
    fn tick_renders_state_fed_widgets_driven_through_widget_mut() {
        let mut panel = Panel::new(
            vec![Box::new(Workspaces::new(Section::Left, 3))],
            Box::new(Tsrc::default()),
            bar(1, 1000),
        )
        .with_width(10);

        let mut backend = HeadlessBackend::new(10);
        let f1 = panel.tick(&mut backend);
        assert_eq!(crate::headless::render_frame(&f1), "123       ");

        // Driving the widget through its id + concrete type changes the bar.
        panel
            .widget_mut::<Workspaces>("workspaces")
            .unwrap()
            .set_state(vec![
                WorkspaceState::Focused,
                WorkspaceState::Visible,
                WorkspaceState::Empty,
            ]);
        let f2 = panel.tick(&mut backend);
        assert_eq!(crate::headless::render_frame(&f2), "◉●3       ");
    }

    /// The state-fed channel is typed and id-checked: a wrong type or a wrong
    /// id is `None`, never a panic, and never reaches a different widget.
    #[test]
    fn widget_mut_requires_the_matching_id_and_type() {
        let mut panel = Panel::new(
            vec![
                Box::new(Workspaces::new(Section::Left, 2)),
                Box::new(Text::new(Section::Right, "hi")),
            ],
            Box::new(Tsrc::default()),
            bar(1, 1000),
        );

        // The right id + right type reaches the workspaces widget.
        let ws: Option<&mut Workspaces> = panel.widget_mut("workspaces");
        assert_eq!(ws.unwrap().count(), 2);

        // The right id, the wrong type.
        assert!(panel.widget_mut::<Text>("workspaces").is_none());

        // The right type, the wrong id (`Text`'s id is `text`, not `hi`).
        assert!(panel.widget_mut::<Text>("hi").is_none());
        // ...and the matching one works.
        assert_eq!(panel.widget_mut::<Text>("text").unwrap().text(), "hi");

        // An id that no widget has.
        assert!(panel.widget_mut::<Workspaces>("battery").is_none());
    }

    /// The gap setting flows from `BarConfig` into the layout.
    #[test]
    fn tick_respects_the_configured_gap() {
        let three_left = |gap: u32| -> String {
            let mut p = Panel::new(
                vec![
                    Box::new(Text::new(Section::Left, "a")),
                    Box::new(Text::new(Section::Left, "b")),
                    Box::new(Text::new(Section::Left, "c")),
                ],
                Box::new(Tsrc::default()),
                bar(gap, 1000),
            )
            .with_width(10);
            let mut b = HeadlessBackend::new(10);
            crate::headless::render_frame(&p.tick(&mut b))
        };
        // Hand-computed on width 10: gap 1 → a@0, b@2, c@4 (cells a_ b_ c
        // followed by 5 spaces); gap 2 → a@0, b@3, c@6.
        assert_eq!(three_left(1), "a b c     ");
        assert_eq!(three_left(2), "a  b  c   ");

        // The gap also decides overflow — within a section: three 2-cell
        // LEFT widgets on width 7. (There is no gap *between* sections, so
        // cross-section cases can't exercise this.)
        // gap 1: 2+2+2 + 2 gaps = 8 > 7 → the outermost-left widget is
        // dropped, leaving "cd"@0, "ef"@3 → "cd ef   ".
        // gap 0: 2+2+2 = 6 ≤ 7 → all fit: "ab"@0, "cd"@2, "ef"@4.
        let fit = |gap: u32| -> String {
            let mut p = Panel::new(
                vec![
                    Box::new(Text::new(Section::Left, "ab")),
                    Box::new(Text::new(Section::Left, "cd")),
                    Box::new(Text::new(Section::Left, "ef")),
                ],
                Box::new(Tsrc::default()),
                bar(gap, 1000),
            )
            .with_width(7);
            let mut b = HeadlessBackend::new(7);
            crate::headless::render_frame(&p.tick(&mut b))
        };
        // (Width 7: "cd"=2 + gap=1 + "ef"=2 → 2 trailing spaces.)
        assert_eq!(fit(1), "cd ef  ");
        assert_eq!(fit(0), "abcdef ");
    }

    /// Full session: open(10) → tick → Tick → tick → Resize{5} → tick →
    /// Quit → close. The resize takes effect on the NEXT tick (frame 3 is
    /// laid out on width 5, not 10).
    #[test]
    fn run_loops_until_quit_and_applies_resizes_on_the_next_tick() {
        let panel = Panel::new(
            vec![
                Box::new(Text::new(Section::Left, "ab")),
                Box::new(Text::new(Section::Right, "ef")),
            ],
            Box::new(Tsrc::default()),
            bar(1, 250),
        );
        let mut backend = HeadlessBackend::with_events(
            10,
            vec![
                BackendEvent::Tick,
                BackendEvent::Resize { width_cells: 5 },
                BackendEvent::Quit,
            ],
        );

        let report = panel.run(&mut backend).unwrap();
        assert_eq!(report.frames, 3);
        assert_eq!(report.width, 5);
        assert_eq!(backend.open_calls, 1);
        assert_eq!(backend.close_calls, 1);
        assert_eq!(backend.wait_timeouts, vec![Duration::from_millis(250); 3]);

        // Frame 1: width 10 — "ab" x=0, "ef" x=8.
        assert_eq!(backend.frame_text(0), "ab      ef");
        // Frame 2: width 10 again (resize not applied yet).
        assert_eq!(backend.frame_text(1), "ab      ef");
        // Frame 3: width 5 — hand-computed: "ab" x=0, "ef" x=3 → "ab ef".
        assert_eq!(backend.frame_text(2), "ab ef");
    }

    /// A backend that quits immediately still produces exactly one frame and
    /// closes cleanly.
    #[test]
    fn run_paints_one_frame_before_an_immediate_quit() {
        let panel = Panel::new(
            vec![Box::new(Text::new(Section::Right, "x"))],
            Box::new(Tsrc::default()),
            bar(1, 1000),
        );
        let mut backend = HeadlessBackend::with_events(10, vec![BackendEvent::Quit]);
        let report = panel.run(&mut backend).unwrap();
        assert_eq!(report.frames, 1);
        assert_eq!(backend.paints().len(), 1);
        assert_eq!(backend.frame_text(0), "         x");
        assert_eq!(backend.close_calls, 1);
    }

    /// An `open` failure propagates: no frames, no close (nothing to release).
    #[test]
    fn run_propagates_open_errors_without_painting_or_closing() {
        struct FailingBackend;
        impl Backend for FailingBackend {
            fn open(&mut self) -> Result<u32, BackendError> {
                Err(BackendError::Connect("WAYLAND_DISPLAY not set".to_string()))
            }
            fn wait(&mut self, _timeout: Duration) -> BackendEvent {
                BackendEvent::Tick
            }
            fn paint(&mut self, _frame: &Frame) {}
            fn close(&mut self) {}
        }

        let panel = Panel::new(
            vec![Box::new(Text::new(Section::Left, "ab"))],
            Box::new(Tsrc::default()),
            bar(1, 1000),
        );
        let mut backend = FailingBackend;
        let err = panel.run(&mut backend).unwrap_err();
        assert_eq!(
            err,
            BackendError::Connect("WAYLAND_DISPLAY not set".to_string())
        );
        assert!(err.to_string().contains("WAYLAND_DISPLAY"));
    }

    /// Real built-in widgets, headless: a Clock at a fixed epoch renders the
    /// same string on every tick — the full pipeline (source → widget →
    /// layout → frame) is deterministic end to end.
    #[test]
    fn run_with_a_real_clock_is_deterministic_at_a_fixed_epoch() {
        let panel = Panel::new(
            vec![Box::new(widgets::Clock::new(Section::Right, "%H:%M"))],
            Box::new(Tsrc(std::sync::Arc::new(std::sync::Mutex::new(TsrcInner {
                now: Some(1_700_000_000),
                tz: Some(3600), // UTC+01:00
                ..Default::default()
            })))),
            bar(1, 1000),
        );
        let mut backend =
            HeadlessBackend::with_events(12, vec![BackendEvent::Tick, BackendEvent::Quit]);
        let report = panel.run(&mut backend).unwrap();
        assert_eq!(report.frames, 2);
        // 1_700_000_000 s = 2023-11-14T22:13:20 UTC → 23:13 in UTC+1.
        // Width 12, "23:13" is 5 cells → x = 7: seven leading spaces.
        assert_eq!(backend.frame_text(0), "       23:13");
        assert_eq!(backend.frame_text(1), "       23:13");
    }

    /// An empty panel still runs the full loop: frames are painted, just
    /// blank.
    #[test]
    fn run_with_no_widgets_paints_blank_frames() {
        let panel = Panel::new(Vec::new(), Box::new(Tsrc::default()), bar(1, 1000));
        let mut backend = HeadlessBackend::with_events(8, vec![BackendEvent::Quit]);
        let report = panel.run(&mut backend).unwrap();
        assert_eq!(report.frames, 1);
        assert!(backend.paints()[0].is_empty());
        assert_eq!(backend.frame_text(0), "        ");
    }
}
