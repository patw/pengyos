//! The terminal front-end: the Sensors table and the Hardware inventory, for the
//! Server image (no graphical session), an ssh session, or the Linux console.
//!
//! A cut-down sibling of the COSMIC window, not a different program. The data
//! path is the same one (`hwall-core` collects through `MonitorWorker`,
//! `hwall-app` builds the rows), and so is the colour policy: [`tjmax`] decides
//! the limit a reading is measured against and how close is too close. What is
//! left out is the Limits editor; rules saved by the Desktop app (or by hand in
//! `~/.config/hwall/settings.json`) are read and still colour the table.
//!
//! Drawn for the Linux console first: 16 colours (a truecolor rule colour is
//! mapped down to red or yellow), no mouse, and it works at 80x25.

use crate::tjmax::{self, is_ok_status, sensor_limit, Heat, Limit};
use hwall_app::{
    build_hardware_inventory, build_sensor_rows, hardware_inventory_text, AppSettings, RowKind,
    RowOptions, SensorRow, SettingsStore,
};
use hwall_core::{
    CollectOptions, CollectionProfile, MonitorCollector, MonitorPoll, MonitorRequestResult,
    MonitorWorker, Snapshot, SnapshotStatistics,
};
use ratatui::crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Clear, Paragraph};
use ratatui::{DefaultTerminal, Frame};
use std::collections::{BTreeMap, HashMap};
use std::io;
use std::time::{Duration, Instant};

/// Width of each number column (Value, Min, Max), including its leading space.
const NUM_W: usize = 11;
/// Below this many columns only Value is shown.
const NARROW: usize = 64;
const INTERVALS: [f64; 6] = [0.5, 1.0, 2.0, 3.0, 5.0, 10.0];
const DEFAULT_INTERVAL: usize = 2; // 2 s: a server is watched, not raced
const INPUT_POLL: Duration = Duration::from_millis(100);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum View {
    Sensors,
    Hardware,
}

/// Entry point called by `main` for `--tui` (or whenever there is no display).
pub fn run(hardware: bool) -> io::Result<()> {
    eprintln!("pengyos-hwmon: discovering hardware...");
    let mut app = Tui::new(if hardware {
        View::Hardware
    } else {
        View::Sensors
    })?;
    ratatui::run(|terminal| app.run_loop(terminal))
}

struct Tui {
    worker: Option<MonitorWorker>,
    failure: Option<String>,
    snapshot: Snapshot,
    statistics: SnapshotStatistics,
    settings: AppSettings,
    rows: Vec<SensorRow>,
    values: HashMap<(String, String), f64>,
    limits: HashMap<(String, String), Limit>,
    device_limits: HashMap<String, Limit>,
    hardware_lines: Vec<String>,
    view: View,
    scroll: usize,
    body_height: usize,
    filter: String,
    editing_filter: bool,
    paused: bool,
    interval: usize,
    requested_at: Option<Instant>,
    in_flight: bool,
    help: bool,
    quit: bool,
}

impl Tui {
    fn new(view: View) -> io::Result<Self> {
        let collector = MonitorCollector::new(
            CollectOptions {
                profile: CollectionProfile::Full,
                allow_helper_commands: true,
                include_sensitive: false,
                include_storage_health: false,
            },
            Duration::from_secs(90),
            Duration::from_secs(1800),
        );
        let snapshot = collector.initial_snapshot();
        let worker = MonitorWorker::spawn(collector)?;
        let mut statistics = SnapshotStatistics::new();
        statistics.observe(&snapshot);
        // Read-only: aliases, hidden sensors and alert rules saved by the Desktop
        // app apply here too. The terminal view never writes the file.
        let settings = SettingsStore::discover().load();
        let mut app = Self {
            worker: Some(worker),
            failure: None,
            snapshot,
            statistics,
            settings,
            rows: Vec::new(),
            values: HashMap::new(),
            limits: HashMap::new(),
            device_limits: HashMap::new(),
            hardware_lines: Vec::new(),
            view,
            scroll: 0,
            body_height: 1,
            filter: String::new(),
            editing_filter: false,
            paused: false,
            interval: DEFAULT_INTERVAL,
            requested_at: None,
            in_flight: false,
            help: false,
            quit: false,
        };
        app.rebuild();
        Ok(app)
    }

    fn run_loop(&mut self, terminal: &mut DefaultTerminal) -> io::Result<()> {
        let mut dirty = true;
        while !self.quit {
            dirty |= self.pump();
            if dirty {
                terminal.draw(|frame| self.draw(frame))?;
                dirty = false;
            }
            if event::poll(INPUT_POLL)? {
                match event::read()? {
                    Event::Key(key)
                        if matches!(key.kind, KeyEventKind::Press | KeyEventKind::Repeat) =>
                    {
                        self.handle_key(key);
                        dirty = true;
                    }
                    Event::Resize(_, _) => dirty = true,
                    _ => {}
                }
            }
        }
        Ok(())
    }

    /// Take a finished sample if there is one, and ask for the next when due.
    /// Returns whether anything visible changed.
    fn pump(&mut self) -> bool {
        let Some(worker) = &self.worker else {
            return false;
        };
        let mut changed = false;
        match worker.poll() {
            MonitorPoll::Update(update) => {
                self.in_flight = false;
                if !self.paused {
                    // overlay() keeps last-known values for sensors a slow
                    // collector skipped this round, as the window does.
                    self.snapshot = self.snapshot.overlay(update.snapshot);
                    self.statistics.observe(&self.snapshot);
                    self.rebuild();
                    changed = true;
                }
            }
            MonitorPoll::Idle => {}
            MonitorPoll::Disconnected => {
                self.fail("collector disconnected");
                return true;
            }
        }
        let due = self
            .requested_at
            .is_none_or(|at| at.elapsed() >= self.interval());
        if !self.paused && !self.in_flight && due {
            if let Some(worker) = &self.worker {
                if worker.request(false) == MonitorRequestResult::Disconnected {
                    self.fail("collector disconnected");
                    return true;
                }
            }
            self.in_flight = true;
            self.requested_at = Some(Instant::now());
        }
        changed
    }

    fn fail(&mut self, why: &str) {
        self.worker = None;
        self.failure = Some(why.to_owned());
    }

    fn interval(&self) -> Duration {
        Duration::from_secs_f64(INTERVALS[self.interval])
    }

    /// Rows and limits from the latest snapshot: the same calls the window makes.
    fn rebuild(&mut self) {
        self.rows = build_sensor_rows(
            &self.snapshot,
            &self.statistics,
            RowOptions {
                visibility: &self.settings.visibility,
                device_aliases: &self.settings.device_aliases,
                sensor_aliases: &self.settings.sensor_aliases,
                device_order: &self.settings.device_order,
                show_sensor_groups: self.settings.show_sensor_groups,
                query: &self.filter,
                favorites_only: false,
                alert_rules: &self.settings.sensor_alerts,
                alert_states: &BTreeMap::new(),
            },
        );
        self.values.clear();
        self.limits.clear();
        self.device_limits.clear();
        for device in &self.snapshot.devices {
            for sensor in &device.sensors {
                let key = (device.id.clone(), sensor.id.clone());
                if let Some(value) = sensor.value {
                    self.values.insert(key.clone(), value);
                }
                if let Some(limit) = sensor_limit(device, sensor) {
                    self.limits.insert(key, limit);
                }
            }
            if let Some(limit) = tjmax::device_limit(device) {
                self.device_limits.insert(device.id.clone(), limit);
            }
        }
        if self.view == View::Hardware {
            let inventory = build_hardware_inventory(
                &self.snapshot,
                &self.statistics,
                &self.settings.device_aliases,
                &self.settings.sensor_aliases,
                &self.settings.sensor_alerts,
                &BTreeMap::new(),
            );
            self.hardware_lines = hardware_inventory_text(&inventory)
                .lines()
                .map(str::to_owned)
                .collect();
        }
        self.clamp_scroll();
    }

    fn line_count(&self) -> usize {
        match self.view {
            View::Sensors => self.rows.len(),
            View::Hardware => self.hardware_lines.len(),
        }
    }

    fn clamp_scroll(&mut self) {
        let max = self.line_count().saturating_sub(self.body_height);
        self.scroll = self.scroll.min(max);
    }

    fn handle_key(&mut self, key: KeyEvent) {
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
            self.quit = true;
            return;
        }
        if self.help {
            self.help = false;
            return;
        }
        if self.editing_filter {
            match key.code {
                KeyCode::Esc => {
                    self.editing_filter = false;
                    self.filter.clear();
                }
                KeyCode::Enter => self.editing_filter = false,
                KeyCode::Backspace => {
                    self.filter.pop();
                }
                KeyCode::Char(c) => self.filter.push(c),
                _ => return,
            }
            self.scroll = 0;
            self.rebuild();
            return;
        }
        let page = self.body_height.saturating_sub(1).max(1);
        match key.code {
            KeyCode::Char('q') => self.quit = true,
            KeyCode::Esc if !self.filter.is_empty() => {
                self.filter.clear();
                self.rebuild();
            }
            KeyCode::Esc => self.quit = true,
            KeyCode::Up | KeyCode::Char('k') => self.scroll = self.scroll.saturating_sub(1),
            KeyCode::Down | KeyCode::Char('j') => self.scroll += 1,
            KeyCode::PageUp | KeyCode::Char('b') => self.scroll = self.scroll.saturating_sub(page),
            KeyCode::PageDown | KeyCode::Char('f') => self.scroll += page,
            KeyCode::Home | KeyCode::Char('g') => self.scroll = 0,
            KeyCode::End | KeyCode::Char('G') => self.scroll = usize::MAX,
            KeyCode::Char('/') => self.editing_filter = true,
            KeyCode::Char('p') | KeyCode::Char(' ') => self.paused = !self.paused,
            KeyCode::Char('r') => {
                self.statistics = SnapshotStatistics::new();
                self.statistics.observe(&self.snapshot);
                self.rebuild();
            }
            KeyCode::Char('+') | KeyCode::Char('=') => {
                self.interval = (self.interval + 1).min(INTERVALS.len() - 1)
            }
            KeyCode::Char('-') => self.interval = self.interval.saturating_sub(1),
            KeyCode::Tab | KeyCode::Char('h') => {
                self.view = match self.view {
                    View::Sensors => View::Hardware,
                    View::Hardware => View::Sensors,
                };
                self.scroll = 0;
                self.rebuild();
            }
            KeyCode::Char('?') => self.help = true,
            _ => {}
        }
        self.clamp_scroll();
    }

    fn draw(&mut self, frame: &mut Frame<'_>) {
        let [top, body, bottom] = Layout::vertical([
            Constraint::Length(1),
            Constraint::Min(1),
            Constraint::Length(1),
        ])
        .areas(frame.area());
        self.body_height = usize::from(body.height).saturating_sub(1).max(1);
        self.clamp_scroll();

        frame.render_widget(Paragraph::new(self.status_line()), top);

        let width = usize::from(body.width);
        let [heading, list] =
            Layout::vertical([Constraint::Length(1), Constraint::Min(0)]).areas(body);
        let lines: Vec<Line<'_>> = match self.view {
            View::Sensors => {
                frame.render_widget(Paragraph::new(column_heading(width)), heading);
                self.rows
                    .iter()
                    .skip(self.scroll)
                    .take(usize::from(list.height))
                    .map(|row| self.row_line(row, width))
                    .collect()
            }
            View::Hardware => {
                frame.render_widget(Paragraph::new(Line::styled(" Hardware", bold())), heading);
                self.hardware_lines
                    .iter()
                    .skip(self.scroll)
                    .take(usize::from(list.height))
                    .map(|text| Line::raw(fit(&format!(" {text}"), width)))
                    .collect()
            }
        };
        let empty = lines.is_empty();
        frame.render_widget(Paragraph::new(lines), list);
        if empty {
            let note = if self.filter.is_empty() {
                " waiting for the first sample..."
            } else {
                " nothing matches the filter (Esc clears it)"
            };
            frame.render_widget(Paragraph::new(Line::styled(note, dim())), list);
        }

        frame.render_widget(Paragraph::new(self.key_line()), bottom);
        if self.help {
            draw_help(frame);
        }
    }

    fn status_line(&self) -> Line<'static> {
        let (state, color) = match (&self.failure, self.paused) {
            (Some(_), _) => ("STOPPED", Color::Red),
            (None, true) => ("PAUSED", Color::Yellow),
            (None, false) => ("LIVE", Color::Green),
        };
        let sensors: usize = self.snapshot.devices.iter().map(|d| d.sensors.len()).sum();
        let mut spans = vec![
            Span::styled(" Hardware Monitor ", bold()),
            Span::styled(
                format!(" {state} "),
                Style::new().fg(Color::Black).bg(color),
            ),
            Span::raw(format!(
                "  {} devices · {sensors} sensors · every {}s",
                self.snapshot.devices.len(),
                INTERVALS[self.interval]
            )),
        ];
        if let Some(why) = &self.failure {
            spans.push(Span::styled(
                format!("  {why}"),
                Style::new().fg(Color::Red),
            ));
        }
        Line::from(spans)
    }

    fn key_line(&self) -> Line<'static> {
        if self.editing_filter {
            return Line::from(vec![
                Span::styled(" Filter: ", bold()),
                Span::raw(format!("{}_", self.filter)),
                Span::styled("   Enter keeps it, Esc clears it", dim()),
            ]);
        }
        let mut spans = Vec::new();
        if !self.filter.is_empty() {
            spans.push(Span::styled(
                format!(" filter \"{}\" ", self.filter),
                Style::new().fg(Color::Black).bg(Color::Cyan),
            ));
        }
        let view = match self.view {
            View::Sensors => "h hardware",
            View::Hardware => "h sensors",
        };
        spans.push(Span::styled(
            format!(" q quit  / filter  p pause  {view}  ? more keys"),
            dim(),
        ));
        Line::from(spans)
    }

    fn row_line<'a>(&self, row: &'a SensorRow, width: usize) -> Line<'a> {
        match row.kind {
            RowKind::Device => {
                let note = device_note(self.device_limits.get(&row.device_id), width);
                let label_w = width.saturating_sub(note.chars().count());
                Line::from(vec![
                    Span::styled(
                        fit(&format!(" {}", row.label), label_w),
                        bold().fg(Color::Cyan),
                    ),
                    Span::styled(note, dim()),
                ])
            }
            RowKind::Header => Line::styled(fit(&format!("   {}", row.label), width), dim()),
            RowKind::Sensor => {
                let key = (
                    row.device_id.clone(),
                    row.sensor_id.clone().unwrap_or_default(),
                );
                let limit = self.limits.get(&key).map(|limit| limit.celsius);
                let current = self.values.get(&key).copied();
                // Same precedence as the window: a configured rule's colour,
                // then the resolved limit, then nothing.
                let value_style = match row.current_color.as_deref().map(console_colour) {
                    Some(colour) => Style::new().fg(colour).add_modifier(Modifier::BOLD),
                    None => match tjmax::heat(current, limit) {
                        Some(Heat::Hot) => Style::new().fg(Color::Red).add_modifier(Modifier::BOLD),
                        Some(Heat::Warm) => Style::new().fg(Color::Yellow),
                        None if row.dimmed => dim(),
                        None => Style::new(),
                    },
                };
                let quiet = if row.dimmed { dim() } else { Style::new() };
                let narrow = width < NARROW;
                let numbers = if narrow { NUM_W } else { NUM_W * 3 };
                let indent = 3 + 2 * usize::from(row.depth);
                let status = if is_ok_status(&row.status) {
                    String::new()
                } else {
                    format!(" {}", row.status)
                };
                let label_w = width.saturating_sub(numbers);
                let text_w = label_w.saturating_sub(status.chars().count());
                let mut spans = vec![
                    Span::styled(fit(&format!("{:indent$}{}", "", row.label), text_w), quiet),
                    Span::styled(
                        fit(&status, label_w - text_w),
                        Style::new().fg(Color::Yellow),
                    ),
                    Span::styled(right(&row.current, NUM_W), value_style),
                ];
                if !narrow {
                    spans.push(Span::styled(right(&row.minimum, NUM_W), quiet));
                    spans.push(Span::styled(right(&row.maximum, NUM_W), quiet));
                }
                Line::from(spans)
            }
        }
    }
}

/// The limit shown at the end of a device's line, as much of it as leaves the
/// device name at least `MIN_NAME` columns: "limit 95 °C · Zen3/Zen3+/Zen4",
/// then "limit 95 °C", then nothing. Starts with a space so it never touches
/// the name.
fn device_note(limit: Option<&Limit>, width: usize) -> String {
    const MIN_NAME: usize = 24;
    let Some(limit) = limit else {
        return String::new();
    };
    let long = format!(" limit {:.0} °C · {} ", limit.celsius, limit.note);
    let short = format!(" limit {:.0} °C ", limit.celsius);
    [long, short]
        .into_iter()
        .find(|note| width >= MIN_NAME + note.chars().count())
        .unwrap_or_default()
}

fn column_heading(width: usize) -> Line<'static> {
    let narrow = width < NARROW;
    let numbers = if narrow { NUM_W } else { NUM_W * 3 };
    let mut text = fit(" Sensor", width.saturating_sub(numbers));
    text.push_str(&right("Value", NUM_W));
    if !narrow {
        text.push_str(&right("Min", NUM_W));
        text.push_str(&right("Max", NUM_W));
    }
    Line::styled(text, bold().add_modifier(Modifier::UNDERLINED))
}

fn draw_help(frame: &mut Frame<'_>) {
    const HELP: [&str; 12] = [
        "Up/Down, j/k        scroll one line",
        "PgUp/PgDn, b/f      scroll one page",
        "Home/End, g/G       top / bottom",
        "/                   filter by name (Esc clears)",
        "p or Space          pause / resume sampling",
        "r                   reset Min and Max",
        "+ / -               sample less / more often",
        "h or Tab            sensors <-> hardware inventory",
        "q or Esc            quit",
        "",
        "Amber: within 15 °C of the limit. Red: within 5 °C.",
        "The limit and its source are on each device's line.",
    ];
    let area = frame.area();
    let w = 58.min(area.width);
    let h = (HELP.len() as u16 + 2).min(area.height);
    let popup = Rect {
        x: area.x + (area.width - w) / 2,
        y: area.y + (area.height - h) / 2,
        width: w,
        height: h,
    };
    frame.render_widget(Clear, popup);
    frame.render_widget(
        Paragraph::new(
            HELP.iter()
                .map(|l| Line::raw(format!(" {l}")))
                .collect::<Vec<_>>(),
        )
        .block(
            Block::new()
                .borders(Borders::ALL)
                .title(" Keys (any key closes) "),
        ),
        popup,
    );
}

fn bold() -> Style {
    Style::new().add_modifier(Modifier::BOLD)
}

fn dim() -> Style {
    // DarkGray is "bright black": readable on the Linux console, where DIM is not.
    Style::new().fg(Color::DarkGray)
}

/// Pad or cut `text` to exactly `width` columns.
fn fit(text: &str, width: usize) -> String {
    let count = text.chars().count();
    if count <= width {
        format!("{text}{:pad$}", "", pad = width - count)
    } else if width == 0 {
        String::new()
    } else {
        let mut cut: String = text.chars().take(width - 1).collect();
        cut.push('~');
        cut
    }
}

/// Right-align `text` in `width` columns (cut from the left if it is too long).
fn right(text: &str, width: usize) -> String {
    let count = text.chars().count();
    if count >= width {
        // keep one space between columns; the units end is the informative one
        let tail: String = text.chars().skip(count + 1 - width).collect();
        format!(" {tail}")
    } else {
        format!("{:>width$}", text)
    }
}

/// A rule's `#rrggbb` mapped to the console palette: reddish is red, anything
/// else that a rule colours is a warning, so yellow. The Linux console has no
/// truecolor, and a rule colour only ever means "warning" or "critical".
fn console_colour(hex: &str) -> Color {
    let hex = hex.trim_start_matches('#');
    let channel = |i: usize| u8::from_str_radix(hex.get(i..i + 2).unwrap_or("00"), 16).unwrap_or(0);
    let (r, g) = (u16::from(channel(0)), u16::from(channel(2)));
    if r > 0x99 && g * 3 < r * 2 {
        Color::Red
    } else {
        Color::Yellow
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fit_pads_and_cuts_to_the_column() {
        assert_eq!(fit("cpu", 6), "cpu   ");
        assert_eq!(fit("Package id 0", 8), "Package~");
        assert_eq!(fit("x", 0), "");
        assert_eq!(fit("°C", 2).chars().count(), 2);
    }

    #[test]
    fn right_aligns_and_keeps_the_width() {
        assert_eq!(right("45.0 °C", NUM_W), "    45.0 °C");
        assert_eq!(right("12345678901234", NUM_W).chars().count(), NUM_W);
        assert_eq!(right("12345678901234", NUM_W), " 5678901234");
    }

    #[test]
    fn heading_fits_80_and_40_columns() {
        for width in [80, 40] {
            let line = column_heading(width);
            let text: String = line.spans.iter().map(|s| s.content.as_ref()).collect();
            assert_eq!(text.chars().count(), width, "{width}");
            assert_eq!(text.contains("Min"), width >= NARROW);
        }
    }

    #[test]
    fn device_note_shrinks_before_the_name_does() {
        let limit = Limit {
            celsius: 95.0,
            note: "Zen3/Zen3+/Zen4",
        };
        assert_eq!(
            device_note(Some(&limit), 80),
            " limit 95 °C · Zen3/Zen3+/Zen4 "
        );
        assert_eq!(device_note(Some(&limit), 40), " limit 95 °C ");
        assert_eq!(device_note(Some(&limit), 30), "");
        assert_eq!(device_note(None, 80), "");
    }

    #[test]
    fn key_hints_fit_80_columns_with_a_filter() {
        // the hint line plus a filter tag of a typical length
        let hints = " q quit  / filter  p pause  h hardware  ? more keys";
        assert!(hints.chars().count() + " filter \"temperature\" ".len() <= 80);
    }

    #[test]
    fn rule_colours_map_to_the_console_palette() {
        assert_eq!(console_colour("#e04a3d"), Color::Red);
        assert_eq!(console_colour("#ff0000"), Color::Red);
        assert_eq!(console_colour("#e89e21"), Color::Yellow);
        assert_eq!(console_colour("garbage"), Color::Yellow);
    }
}
