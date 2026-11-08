//! The libcosmic front-end: window, table, limits editor, fans.
//!
//! HWall does all the collection (`hwall-core`) and all the presentation policy
//! (`hwall-app`); this module is only the view + event loop, which is what makes
//! a second front-end (the Server TUI) cheap.

use cosmic::app::{Core, Settings, Task};
use cosmic::iced::core::text::LineHeight;
use cosmic::iced::{Alignment, Color, Length, Padding, Pixels, Size, Subscription};
use cosmic::prelude::*;
use cosmic::theme;
use cosmic::widget;
use hwall_app::{
    build_hardware_inventory, build_sensor_rows, sensor_key, AppSettings, HardwareInventory,
    RowKind, RowOptions, SensorRow, SettingsStore,
};
use hwall_core::{
    CollectOptions, CollectionProfile, MonitorCollector, MonitorPoll, MonitorRequestResult,
    MonitorWorker, Snapshot, SnapshotStatistics, Unit,
};
use std::collections::BTreeMap;
use std::sync::mpsc::{Receiver, TryRecvError};
use std::thread;
use std::time::{Duration, Instant};

use crate::fans::{self, FanStatus};
use crate::tjmax::{self, is_ok_status, sensor_limit, Heat, Limit};

const APP_ID: &str = "io.catbee.pengyos.Hwmon";
const TICK: Duration = Duration::from_millis(250);

/// Entry point called by `main` when the `cosmic` feature is enabled.
pub fn run(args: &[String]) -> cosmic::iced::Result {
    // --view sensors|hardware|limits starts on a given pane (screenshots, and a
    // shortcut for anyone who lives in the limits editor).
    let start = match args.iter().position(|arg| arg == "--view") {
        Some(index) => match args.get(index + 1).map(String::as_str) {
            Some("hardware") => View::Hardware,
            Some("limits") => View::Limits,
            Some("fans") => View::Fans,
            _ => View::Sensors,
        },
        None => View::Sensors,
    };
    let stress = args
        .iter()
        .any(|arg| arg == "--stress-scroll" || arg == "--stress-repaint");

    let settings = Settings::default().size(Size::new(700.0, 620.0));
    cosmic::app::run::<HWall>(settings, (start, stress))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum View {
    Sensors,
    Hardware,
    /// Warning / critical thresholds (the hwall-app alert rules, per sensor).
    Limits,
    /// Fan profiles, through pengyos-fans.
    Fans,
}

/// How often the Fans pane asks pengyos-fans again (it starts a process).
const FANS_EVERY: Duration = Duration::from_secs(2);

const ROLES: [&str; 3] = ["fan", "pump", "none"];
const ROLE_LABELS: [&str; 3] = [
    "A fan: follows the profile",
    "A pump: left alone",
    "Nothing: left alone",
];
/// Index i here is fans::FOLLOWS[i]; a fan without a chip curve skips "chip".
const FOLLOW_LABELS: [&str; 4] = [
    "The chip's own sensor",
    "The CPU",
    "The GPU",
    "CPU or GPU, whichever is hotter",
];
/// When a fan has no curve to start from (the firmware profile): balanced's.
const START_CURVE: [(i64, i64); 4] = [(35, 30), (55, 45), (70, 70), (80, 100)];

/// The editor for one fan channel: what the user has typed, and what it was.
struct FanEdit {
    id: String,
    name: String,
    role: usize,
    /// index into follow_options()
    follow: usize,
    chip_curve: bool,
    /// (temperature, duty) as typed
    points: Vec<(String, String)>,
    /// the curve is the fan's own (false: follow the profile's)
    own: bool,
    curve_dirty: bool,
    max_points: usize,
    min_duty: i64,
    was_name: String,
    was_role: usize,
    was_follow: usize,
    was_own: bool,
}

impl FanEdit {
    fn new(c: &fans::Channel) -> Self {
        let role = ROLES.iter().position(|r| *r == c.role).unwrap_or(2);
        let options = follow_options(c.chip_curve);
        let follow = options
            .iter()
            .position(|&i| fans::FOLLOWS[i] == c.follow)
            .unwrap_or(0);
        let max_points = c.max_points.clamp(2, 8);
        let mut start: Vec<(i64, i64)> = c.curve.clone().unwrap_or_else(|| START_CURVE.to_vec());
        if start.len() > max_points {
            // keep the first points and the last (full speed)
            let last = *start.last().unwrap();
            start.truncate(max_points - 1);
            start.push(last);
        }
        FanEdit {
            id: c.id.clone(),
            name: c.name.clone(),
            role,
            follow,
            chip_curve: c.chip_curve,
            points: start
                .iter()
                .map(|(t, d)| (t.to_string(), d.to_string()))
                .collect(),
            own: c.own_curve,
            curve_dirty: false,
            max_points,
            min_duty: c.min_duty,
            was_name: c.name.clone(),
            was_role: role,
            was_follow: follow,
            was_own: c.own_curve,
        }
    }

    fn parsed(&self) -> Result<Vec<(i64, i64)>, String> {
        self.points
            .iter()
            .map(|(t, d)| match (t.parse(), d.parse()) {
                (Ok(t), Ok(d)) => Ok((t, d)),
                _ => Err("Every point needs a temperature and a speed.".to_owned()),
            })
            .collect()
    }

    /// The pengyos-fans commands that make it so, in order (role first: a fan
    /// that becomes a pump needs no curve).
    fn commands(&self) -> Result<Vec<Vec<String>>, String> {
        let mut out = Vec::new();
        let name = self.name.trim();
        if name != self.was_name {
            out.push(fans::name_argv(&self.id, name));
        }
        if self.role != self.was_role {
            out.push(fans::role_argv(&self.id, ROLES[self.role]));
        }
        if ROLES[self.role] != "fan" {
            return Ok(out);
        }
        if self.follow != self.was_follow {
            let i = follow_options(self.chip_curve)[self.follow];
            out.push(fans::follow_argv(&self.id, fans::FOLLOWS[i]));
        }
        if self.curve_dirty {
            if self.own {
                let points = self.parsed()?;
                fans::check_curve(&points, self.max_points)?;
                out.push(fans::curve_argv(&self.id, &points));
            } else if self.was_own {
                out.push(fans::curve_argv(&self.id, &[]));
            }
        }
        Ok(out)
    }
}

/// The editor under a fan's row: name, role, what it follows, its curve.
fn fan_editor<'a>(edit: &'a FanEdit, profile: &str, busy: bool) -> Element<'a, Message> {
    let label = |text: &'static str| {
        widget::text::body(text)
            .width(Length::Fixed(150.0))
            .class(theme::Text::Color(dimmed()))
    };
    let mut col = widget::column::with_capacity(10)
        .spacing(8)
        .padding([8, 24]);
    col = col.push(
        widget::row::with_capacity(2)
            .push(label("Name"))
            .push(
                widget::text_input("e.g. Front intake", edit.name.as_str())
                    .on_input(Message::EditName)
                    .width(Length::Fixed(260.0)),
            )
            .align_y(Alignment::Center),
    );
    col = col.push(
        widget::row::with_capacity(2)
            .push(label("This control drives"))
            .push(widget::dropdown(
                &ROLE_LABELS[..],
                Some(edit.role),
                Message::EditRole,
            ))
            .align_y(Alignment::Center),
    );
    if ROLES[edit.role] == "fan" {
        let options: Vec<&'static str> = follow_options(edit.chip_curve)
            .into_iter()
            .map(|i| FOLLOW_LABELS[i])
            .collect();
        col = col.push(
            widget::row::with_capacity(2)
                .push(label("Speed follows"))
                .push(widget::dropdown(
                    options,
                    Some(edit.follow),
                    Message::EditFollow,
                ))
                .align_y(Alignment::Center),
        );
        let heading = if edit.own {
            "Its own curve (°C → speed)".to_owned()
        } else {
            format!("The {profile} profile's curve (°C → speed); change a point to make it this fan's own")
        };
        col = col.push(widget::text::caption_heading(heading));
        for (i, (t, d)) in edit.points.iter().enumerate() {
            let mut row = widget::row::with_capacity(6)
                .spacing(6)
                .align_y(Alignment::Center)
                .push(
                    widget::text_input("°C", t.as_str())
                        .on_input(move |s| Message::EditPoint(i, true, s))
                        .width(Length::Fixed(64.0)),
                )
                .push(widget::text::body("°C →"))
                .push(
                    widget::text_input("%", d.as_str())
                        .on_input(move |s| Message::EditPoint(i, false, s))
                        .width(Length::Fixed(64.0)),
                )
                .push(widget::text::body("%"));
            if edit.points.len() > 2 && i + 1 < edit.points.len() {
                row = row.push(widget::button::text("Remove").on_press(Message::RemovePoint(i)));
            }
            col = col.push(row);
        }
        let mut curve_buttons = widget::row::with_capacity(2).spacing(8);
        if edit.points.len() < edit.max_points.min(8) {
            curve_buttons =
                curve_buttons.push(widget::button::text("Add a point").on_press(Message::AddPoint));
        }
        if edit.own {
            curve_buttons = curve_buttons.push(
                widget::button::text("Use the profile's curve").on_press(Message::ProfileCurve),
            );
        }
        col = col.push(curve_buttons);
        let mut notes = vec![if edit.chip_curve {
            format!(
                "The last point must be 100 %. This fan's chip holds {} points.",
                edit.max_points
            )
        } else {
            "The last point must be 100 %. Up to 8 points.".to_owned()
        }];
        if profile == "firmware" {
            notes.push("Curves apply under Quiet and Balanced; the profile is \"as the firmware set it\" now.".into());
        }
        if edit.min_duty > 20 {
            notes.push(format!(
                "This fan stops below {}%, so it never runs slower than {}%.",
                edit.min_duty - 5,
                edit.min_duty
            ));
        }
        if !edit.chip_curve {
            notes.push(
                "A service runs this fan's curve; if it stops, the fan goes to full speed.".into(),
            );
        }
        for note in notes {
            col = col.push(widget::text::caption(note).class(theme::Text::Color(dimmed())));
        }
    }
    let save = widget::button::suggested("Save");
    col = col.push(
        widget::row::with_capacity(2)
            .spacing(8)
            .push(if busy {
                save
            } else {
                save.on_press(Message::SaveFan)
            })
            .push(widget::button::standard("Cancel").on_press(Message::EditFan(None))),
    );
    col.into()
}

/// Indices into fans::FOLLOWS a channel may choose from.
fn follow_options(chip_curve: bool) -> Vec<usize> {
    if chip_curve {
        vec![0, 1, 2, 3]
    } else {
        vec![1, 2, 3]
    }
}

#[derive(Debug, Clone)]
enum Message {
    Tick,
    /// Synthetic scroll, only used by --stress-scroll for measurement.
    StressTick,
    /// Viewport change from the list's scrollable (drives virtualization).
    Scrolled(cosmic::iced::widget::scrollable::Viewport),
    SetView(View),
    TogglePause,
    ClearStatistics,
    /// Warning threshold for one sensor key, in the sensor's own unit.
    SetWarning(String, f64),
    /// Critical threshold for one sensor key.
    SetCritical(String, f64),
    /// Drop the rule for one sensor key (back to the resolved limit only).
    ClearRule(String),
    FansLoaded(Result<FanStatus, String>),
    SetFanProfile(&'static str),
    DetectFans,
    /// Open (or close, with None) the editor for one fan channel.
    EditFan(Option<String>),
    EditName(String),
    EditRole(usize),
    EditFollow(usize),
    /// (point, is the temperature, the text typed)
    EditPoint(usize, bool, String),
    AddPoint,
    RemovePoint(usize),
    /// Drop the fan's own curve: it follows the profile's again.
    ProfileCurve,
    SaveFan,
    /// A pkexec pengyos-fans command finished; Err is what to show.
    FansDone(Result<(), String>),
}

/// Where the sampling worker is in its lifecycle. The first full discovery
/// takes seconds (hwmon walk + helper processes), so it never runs on the UI
/// thread.
enum Sampler {
    Booting(Receiver<MonitorWorker>),
    Ready(MonitorWorker),
    Failed(String),
}

struct HWall {
    core: Core,
    sampler: Sampler,
    snapshot: Snapshot,
    statistics: SnapshotStatistics,
    settings: AppSettings,
    /// kept so the settings dialog (not yet ported) can persist changes
    #[allow(dead_code)]
    store: SettingsStore,
    rows: Vec<SensorRow>,
    /// device id -> human name, for the table's first column
    device_names: std::collections::HashMap<String, String>,
    /// (device id, sensor id) -> current numeric value, for limit colouring
    values: std::collections::HashMap<(String, String), f64>,
    /// (device id, sensor id) -> the limit the colour is measured against
    limits: std::collections::HashMap<(String, String), Limit>,
    /// device id -> (limit, note) shown in the section header
    device_limits: std::collections::HashMap<String, Limit>,
    /// top of each row, in content coordinates (virtualization)
    row_tops: Vec<f32>,
    list_height: f32,
    /// scroll offset and viewport height, reported by the scrollable
    scroll_y: f32,
    viewport_h: f32,
    inventory: Option<HardwareInventory>,
    fans: Option<Result<FanStatus, String>>,
    fans_at: Option<Instant>,
    /// what a running pkexec command is doing ("Detecting fans...")
    fans_busy: Option<&'static str>,
    fans_error: Option<String>,
    fan_edit: Option<FanEdit>,
    view: View,
    interval: Duration,
    last_update: Option<Instant>,
    paused: bool,
    stress: bool,
    stress_scroll: bool,
    stress_ticks: u32,
}

impl HWall {
    /// Spawn the discovery + sampling worker off-thread. `MonitorWorker` and
    /// `MonitorCollector` are the same ones the GTK front-end uses, so sample
    /// coalescing, staleness marking and rediscovery behave identically.
    fn start_sampler(rediscover: Duration, health_interval: Duration) -> Sampler {
        let (tx, rx) = std::sync::mpsc::channel();
        let spawned = thread::Builder::new()
            .name("hwall-collector".to_owned())
            .spawn(move || {
                let collector = MonitorCollector::new(
                    CollectOptions {
                        profile: CollectionProfile::Full,
                        allow_helper_commands: true,
                        include_sensitive: false,
                        include_storage_health: false,
                    },
                    rediscover,
                    health_interval,
                );
                match MonitorWorker::spawn(collector) {
                    Ok(worker) => {
                        let _ = tx.send(worker);
                    }
                    Err(error) => eprintln!("hwall-cosmic: collector worker failed: {error}"),
                }
            });
        match spawned {
            Ok(_) => Sampler::Booting(rx),
            Err(error) => Sampler::Failed(error.to_string()),
        }
    }

    /// Rebuild presentation rows from the latest snapshot — the same call the
    /// GTK front-end makes. Labels, units, min/max/avg, alert colours and the
    /// grouping tree all come from `hwall-app`, never from this file.
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
                query: "",
                favorites_only: self.settings.favorites_only,
                alert_rules: &self.settings.sensor_alerts,
                alert_states: &BTreeMap::new(),
            },
        );
        self.device_names = self
            .snapshot
            .devices
            .iter()
            .map(|device| (device.id.clone(), device.name.clone()))
            .collect();
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
        self.row_tops.clear();
        let mut top = 0.0;
        for row in &self.rows {
            self.row_tops.push(top);
            top += row_height(row.kind);
        }
        self.list_height = top;

        if self.view == View::Hardware {
            self.inventory = Some(build_hardware_inventory(
                &self.snapshot,
                &self.statistics,
                &self.settings.device_aliases,
                &self.settings.sensor_aliases,
                &self.settings.sensor_alerts,
                &BTreeMap::new(),
            ));
        }
    }

    fn pump(&mut self) {
        if let Sampler::Booting(rx) = &self.sampler {
            match rx.try_recv() {
                Ok(worker) => {
                    let _ = worker.request(false);
                    self.sampler = Sampler::Ready(worker);
                }
                Err(TryRecvError::Empty) => {}
                Err(TryRecvError::Disconnected) => {
                    self.sampler = Sampler::Failed("collector thread exited".to_owned());
                }
            }
        }

        if self.paused {
            return;
        }

        let Sampler::Ready(worker) = &self.sampler else {
            return;
        };
        match worker.poll() {
            MonitorPoll::Update(update) => {
                // overlay() keeps last-known values for sensors a slow collector
                // skipped this round — the tested path, not a naive replace.
                self.snapshot = self.snapshot.overlay(update.snapshot);
                self.statistics.observe(&self.snapshot);
                self.last_update = Some(Instant::now());
                self.rebuild();
            }
            MonitorPoll::Idle => {
                if self
                    .last_update
                    .is_none_or(|at| at.elapsed() >= self.interval)
                {
                    if worker.request(false) == MonitorRequestResult::Disconnected {
                        self.sampler = Sampler::Failed("collector disconnected".to_owned());
                    }
                }
            }
            MonitorPoll::Disconnected => {
                self.sampler = Sampler::Failed("collector disconnected".to_owned());
            }
        }
    }

    fn persist(&mut self) {
        if let Err(error) = self.store.save(&self.settings) {
            eprintln!("hwall-cosmic: could not save settings: {error}");
        }
        self.rebuild();
    }

    fn status_text(&self) -> (String, String) {
        match &self.sampler {
            Sampler::Booting(_) => ("Discovering hardware…".to_owned(), String::new()),
            Sampler::Failed(error) => ("Collector stopped".to_owned(), error.clone()),
            Sampler::Ready(_) if self.paused => ("Paused".to_owned(), "no sampling".to_owned()),
            Sampler::Ready(_) => (
                "Live".to_owned(),
                format!(
                    "{} sensors · {} rows · {:.1}s interval",
                    self.sensor_count(),
                    self.rows.len(),
                    self.interval.as_secs_f64()
                ),
            ),
        }
    }

    fn sensor_count(&self) -> usize {
        self.snapshot
            .devices
            .iter()
            .map(|device| device.sensors.len())
            .sum()
    }

    fn sensors_view(&self) -> Element<'_, Message> {
        let header = widget::row::with_capacity(5)
            .push(widget::text::caption_heading("Sensor").width(Length::Fixed(LABEL_W)))
            .push(col_heading("Value"))
            .push(col_heading("Min"))
            .push(col_heading("Max"))
            .push(widget::Space::new().width(Length::Fill))
            .push(widget::Space::new().width(Length::Fixed(SCROLLBAR_W)))
            .padding([2, 6])
            .width(Length::Fill);

        // ---- virtualization ------------------------------------------------
        // Only the rows inside the viewport (plus one either side) are built.
        // Every row has a fixed height, so row_tops is an exact cumulative
        // table and the spacers reproduce the full scroll length.
        let offset = self.scroll_y.max(0.0);
        let viewport = if self.viewport_h > 1.0 {
            self.viewport_h
        } else {
            480.0
        };
        let total = self.rows.len();
        let start = match self.row_tops.binary_search_by(|top| {
            top.partial_cmp(&offset)
                .unwrap_or(std::cmp::Ordering::Equal)
        }) {
            Ok(index) => index,
            Err(index) => index.saturating_sub(1),
        }
        .saturating_sub(1);
        let mut end = start;
        while end < total && self.row_tops[end] < offset + viewport + ROW_H {
            end += 1;
        }

        let mut list = widget::column::with_capacity(end - start + 2).spacing(0);
        if start > 0 {
            list = list.push(widget::Space::new().height(Length::Fixed(self.row_tops[start])));
        }

        for row in self.rows.iter().take(end).skip(start) {
            let height = row_height(row.kind);
            let element: Element<'_, Message> = match row.kind {
                RowKind::Device => {
                    let limit = self
                        .device_limits
                        .get(&row.device_id)
                        .map(|limit| format!("limit {:.0} °C · {}", limit.celsius, limit.note))
                        .unwrap_or_default();
                    widget::container(
                        widget::row::with_capacity(2)
                            .push(
                                widget::text::body(shorten(&row.label, 72))
                                    .size(13.0)
                                    .line_height(LineHeight::Absolute(Pixels(16.0)))
                                    .class(theme::Text::Color(accent()))
                                    .width(Length::Fixed(LABEL_W)),
                            )
                            .push(
                                widget::text::caption(limit)
                                    .size(11.0)
                                    .line_height(LineHeight::Absolute(Pixels(16.0)))
                                    .align_x(Alignment::End)
                                    .width(Length::Fixed(NUM * 3.0))
                                    .class(theme::Text::Color(dimmed())),
                            )
                            .push(widget::Space::new().width(Length::Fill))
                            .push(widget::Space::new().width(Length::Fixed(SCROLLBAR_W)))
                            .width(Length::Fill),
                    )
                    .height(Length::Fixed(height))
                    .align_y(Alignment::Center)
                    .padding([0, 6])
                    .width(Length::Fill)
                    .into()
                }
                RowKind::Header => widget::container(
                    widget::text::caption_heading(row.label.clone())
                        .size(11.0)
                        .line_height(LineHeight::Absolute(Pixels(16.0)))
                        .class(theme::Text::Color(dimmed()))
                        .width(Length::Fill),
                )
                .height(Length::Fixed(height))
                .align_y(Alignment::Center)
                .padding([0, 14])
                .width(Length::Fill)
                .into(),
                RowKind::Sensor => {
                    let key = (
                        row.device_id.clone(),
                        row.sensor_id.clone().unwrap_or_default(),
                    );
                    let limit = self.limits.get(&key).map(|limit| limit.celsius);
                    let current = self.values.get(&key).copied();
                    let indent = 6.0 + f32::from(row.depth) * 12.0;

                    // Precedence: a rule the user configured wins, then the
                    // resolved TJ Max, then the plain theme colour.
                    let value_color = row
                        .current_color
                        .as_deref()
                        .map(parse_hex)
                        .or_else(|| heat(current, limit));

                    let mut label = widget::row::with_capacity(2).push(
                        widget::text::body(shorten(&row.label, 64))
                            .size(12.0)
                            .line_height(LineHeight::Absolute(Pixels(16.0)))
                            .width(Length::Fill),
                    );
                    if !is_ok_status(&row.status) {
                        label = label.push(
                            widget::text::caption(row.status.clone())
                                .size(11.0)
                                .line_height(LineHeight::Absolute(Pixels(16.0)))
                                .class(theme::Text::Color(warn())),
                        );
                    }

                    widget::container(
                        widget::row::with_capacity(5)
                            .push(
                                widget::container(label)
                                    .padding(Padding {
                                        top: 0.0,
                                        right: 0.0,
                                        bottom: 0.0,
                                        left: indent,
                                    })
                                    .width(Length::Fixed(LABEL_W)),
                            )
                            .push(num_cell(row.current.clone(), value_color, row.dimmed))
                            .push(num_cell(row.minimum.clone(), None, row.dimmed))
                            .push(num_cell(row.maximum.clone(), None, row.dimmed))
                            .push(widget::Space::new().width(Length::Fill))
                            .push(widget::Space::new().width(Length::Fixed(SCROLLBAR_W)))
                            .width(Length::Fill),
                    )
                    .height(Length::Fixed(height))
                    .align_y(Alignment::Center)
                    .padding([0, 6])
                    .width(Length::Fill)
                    .into()
                }
            };
            list = list.push(element);
        }

        if end < total {
            let consumed = match end.checked_sub(1).and_then(|last| self.row_tops.get(last)) {
                Some(top) => top + row_height(self.rows[end - 1].kind),
                None => 0.0,
            };
            list =
                list.push(widget::Space::new().height(Length::Fixed(self.list_height - consumed)));
        }

        widget::column::with_capacity(2)
            .push(header)
            .push(
                widget::scrollable(list)
                    .id(cosmic::iced::id::Id::new(LIST_ID))
                    .on_scroll(Message::Scrolled)
                    .height(Length::Fill),
            )
            .height(Length::Fill)
            .width(Length::Fill)
            .into()
    }

    /// Per-sensor warning / critical thresholds.
    ///
    /// These are `hwall-app`'s alert rules, the same ones the GTK build edits —
    /// they persist through the shared `SettingsStore` and colour the main table
    /// through `build_sensor_rows`, so there is exactly one threshold model in the
    /// app. A configured rule wins over the resolved TJ Max; with no rule the
    /// TJ Max fallback still provides the colour.
    fn limits_view(&self) -> Element<'_, Message> {
        let mut list = widget::column::with_capacity(self.rows.len() + 8).spacing(0);

        list = list.push(
            widget::row::with_capacity(4)
                .push(widget::text::caption_heading("Sensor").width(Length::Fill))
                .push(widget::text::caption_heading("Warn at").width(Length::Fixed(130.0)))
                .push(widget::text::caption_heading("Crit at").width(Length::Fixed(130.0)))
                .push(widget::text::caption_heading("").width(Length::Fixed(80.0)))
                .padding([2, 6])
                .width(Length::Fill),
        );

        let mut any = false;
        for device in &self.snapshot.devices {
            let mut printed_header = false;
            for sensor in &device.sensors {
                if sensor.unit != Unit::Celsius {
                    continue;
                }
                let Some(limit) = self
                    .limits
                    .get(&(device.id.clone(), sensor.id.clone()))
                    .copied()
                else {
                    continue;
                };
                if !printed_header {
                    list = list.push(
                        widget::row::with_capacity(2)
                            .push(
                                widget::text::body(shorten(&device.name, 60))
                                    .size(13.0)
                                    .line_height(LineHeight::Absolute(Pixels(18.0)))
                                    .class(theme::Text::Color(accent()))
                                    .width(Length::Fill),
                            )
                            .push(
                                widget::text::caption(format!(
                                    "{:.0} °C · {}",
                                    limit.celsius, limit.note
                                ))
                                .size(11.0)
                                .line_height(LineHeight::Absolute(Pixels(18.0)))
                                .align_x(Alignment::End)
                                .width(Length::Fixed(340.0))
                                .class(theme::Text::Color(dimmed())),
                            )
                            .padding([6, 6])
                            .width(Length::Fill),
                    );
                    printed_header = true;
                    any = true;
                }

                let key = sensor_key(&device.id, &sensor.id);
                let rule = self.settings.sensor_alerts.get(&key);
                let warn = rule
                    .and_then(|rule| rule.warning_above)
                    .unwrap_or(limit.celsius - 15.0);
                let crit = rule
                    .and_then(|rule| rule.critical_above)
                    .unwrap_or(limit.celsius - 5.0);

                let warn_key = key.clone();
                let crit_key = key.clone();
                let clear_key = key.clone();

                list = list.push(
                    widget::row::with_capacity(4)
                        .push(
                            widget::text::body(shorten(&sensor.label, 48))
                                .size(12.0)
                                .line_height(LineHeight::Absolute(Pixels(16.0)))
                                .width(Length::Fill),
                        )
                        .push(
                            widget::container(widget::spin_button::spin_button(
                                format!("{warn:.0} °C"),
                                "warning threshold",
                                warn as f32,
                                1.0_f32,
                                0.0_f32,
                                150.0_f32,
                                move |value| {
                                    Message::SetWarning(warn_key.clone(), f64::from(value))
                                },
                            ))
                            .width(Length::Fixed(130.0)),
                        )
                        .push(
                            widget::container(widget::spin_button::spin_button(
                                format!("{crit:.0} °C"),
                                "critical threshold",
                                crit as f32,
                                1.0_f32,
                                0.0_f32,
                                150.0_f32,
                                move |value| {
                                    Message::SetCritical(crit_key.clone(), f64::from(value))
                                },
                            ))
                            .width(Length::Fixed(130.0)),
                        )
                        .push(if rule.is_some() {
                            widget::button::standard("Reset")
                                .on_press(Message::ClearRule(clear_key))
                                .width(Length::Fixed(80.0))
                        } else {
                            widget::button::standard("Reset").width(Length::Fixed(80.0))
                        })
                        .padding([1, 6])
                        .width(Length::Fill),
                );
            }
        }

        if !any {
            return widget::text::body("No temperature sensors to configure.").into();
        }

        widget::scrollable(list).height(Length::Fill).into()
    }

    fn hardware_view(&self) -> Element<'_, Message> {
        let Some(inventory) = &self.inventory else {
            return widget::text::body("Discovering hardware…").into();
        };
        let mut list = widget::column::with_capacity(64).spacing(2);
        for category in &inventory.categories {
            list = list.push(
                widget::text::heading(category.kind.label())
                    .class(theme::Text::Color(accent()))
                    .width(Length::Fill),
            );
            for device in &category.devices {
                list = list.push(
                    widget::column::with_capacity(2)
                        .push(widget::text::body(device.name.clone()))
                        .push(
                            widget::text::caption(device.subtitle.clone())
                                .class(theme::Text::Color(dimmed())),
                        )
                        .padding([2, 12]),
                );
            }
        }
        widget::scrollable(list).height(Length::Fill).into()
    }
}

impl HWall {
    /// Ask pengyos-fans for its status, off the UI thread, when the pane shows.
    fn maybe_load_fans(&mut self) -> Task<Message> {
        if self.view != View::Fans || self.fans_busy.is_some() {
            return Task::none();
        }
        if self.fans_at.is_some_and(|at| at.elapsed() < FANS_EVERY) {
            return Task::none();
        }
        self.fans_at = Some(Instant::now());
        cosmic::task::future(async move {
            let status = thread::spawn(fans::query)
                .join()
                .unwrap_or_else(|_| Err("pengyos-fans status crashed".into()));
            Message::FansLoaded(status)
        })
    }

    /// Run `pkexec pengyos-fans ...` off the UI thread.
    fn fan_command(&mut self, argv: Vec<String>, doing: &'static str) -> Task<Message> {
        self.fan_commands(vec![argv], doing)
    }

    /// Several, in order, stopping at the first that fails.
    fn fan_commands(&mut self, commands: Vec<Vec<String>>, doing: &'static str) -> Task<Message> {
        if self.fans_busy.is_some() {
            return Task::none();
        }
        self.fans_busy = Some(doing);
        self.fans_error = None;
        cosmic::task::future(async move {
            let result = thread::spawn(move || fans::run_all(&commands))
                .join()
                .unwrap_or_else(|_| Err("pengyos-fans crashed".into()));
            Message::FansDone(result)
        })
    }

    fn fans_view(&self) -> Element<'_, Message> {
        let mut page = widget::column::with_capacity(8).spacing(10).padding(12);
        let status = match &self.fans {
            None => {
                return widget::container(widget::text::body("Reading fan controls..."))
                    .padding(12)
                    .height(Length::Fill)
                    .into()
            }
            Some(Err(why)) => {
                return widget::container(widget::text::body(why.clone()))
                    .padding(12)
                    .height(Length::Fill)
                    .into()
            }
            Some(Ok(status)) => status,
        };
        page = page.push(widget::text::body(fans::summary(status)));

        let can_profile = match status.kind {
            fans::Kind::Channels => !status.channels.is_empty(),
            fans::Kind::Platform => true,
            fans::Kind::Nothing => false,
        };
        let busy = self.fans_busy.is_some();
        if can_profile {
            let mut buttons = widget::row::with_capacity(4).spacing(8);
            for name in fans::PROFILES {
                if name == "firmware" && status.kind == fans::Kind::Platform {
                    continue;
                }
                let label = match name {
                    "quiet" => "Quiet",
                    "balanced" => "Balanced",
                    "full" => "Full speed",
                    _ => "As the firmware set it",
                };
                let current = status.kind == fans::Kind::Channels && status.profile == name;
                let button = if current {
                    widget::button::suggested(label)
                } else {
                    widget::button::standard(label)
                };
                buttons = buttons.push(if busy || current {
                    button
                } else {
                    button.on_press(Message::SetFanProfile(name))
                });
            }
            page = page.push(buttons);
        }
        if status.kind == fans::Kind::Channels {
            let label = if status.channels.is_empty() {
                "Detect fans"
            } else {
                "Detect again"
            };
            let button = widget::button::standard(label);
            page = page.push(if busy {
                button
            } else {
                button.on_press(Message::DetectFans)
            });
        }
        if let Some(doing) = self.fans_busy {
            page = page.push(widget::text::caption(doing).class(theme::Text::Color(dimmed())));
        }
        if let Some(why) = &self.fans_error {
            page = page.push(widget::text::caption(why.clone()).class(theme::Text::Color(warn())));
        }

        if !status.channels.is_empty() {
            let heading = widget::row::with_capacity(5)
                .push(widget::text::caption_heading("Fan").width(Length::Fill))
                .push(col_heading("Speed"))
                .push(col_heading("Duty"))
                .push(widget::text::caption_heading("Control").width(Length::Fixed(220.0)))
                .spacing(8)
                .padding([2, 6]);
            let mut table = widget::column::with_capacity(status.channels.len() + 1).push(heading);
            for c in &status.channels {
                let title = if c.name.is_empty() {
                    c.id.clone()
                } else {
                    format!("{} · {}", c.name, c.id)
                };
                let left_alone = c.control == "left alone";
                let editing = self.fan_edit.as_ref().is_some_and(|e| e.id == c.id);
                let edit_button = widget::button::text(if editing { "Close" } else { "Edit" });
                let edit_button = if busy {
                    edit_button
                } else if editing {
                    edit_button.on_press(Message::EditFan(None))
                } else {
                    edit_button.on_press(Message::EditFan(Some(c.id.clone())))
                };
                table = table.push(
                    widget::row::with_capacity(5)
                        .push(widget::text::body(title).width(Length::Fill))
                        .push(num_cell(fans::rpm_text(c), None, left_alone))
                        .push(num_cell(fans::duty_text(c), None, left_alone))
                        .push(
                            widget::text::caption(c.control.clone())
                                .class(theme::Text::Color(dimmed()))
                                .width(Length::Fixed(220.0)),
                        )
                        .push(edit_button)
                        .spacing(8)
                        .padding([2, 6])
                        .align_y(Alignment::Center),
                );
                if editing {
                    if let Some(edit) = &self.fan_edit {
                        table = table.push(fan_editor(edit, &status.profile, busy));
                    }
                }
            }
            page = page.push(table);
        }
        if !status.undetected.is_empty() {
            page = page.push(
                widget::text::caption(format!("Not mapped yet: {}", status.undetected.join(", ")))
                    .class(theme::Text::Color(dimmed())),
            );
        }
        page = page.push(
            widget::text::caption(
                "Changes need an administrator. In a terminal: sudo pengyos-fans --help",
            )
            .class(theme::Text::Color(dimmed())),
        );
        widget::scrollable(page).height(Length::Fill).into()
    }
}

impl cosmic::Application for HWall {
    type Executor = cosmic::executor::Default;
    type Flags = (View, bool);
    type Message = Message;

    const APP_ID: &'static str = APP_ID;

    fn core(&self) -> &Core {
        &self.core
    }

    fn core_mut(&mut self) -> &mut Core {
        &mut self.core
    }

    fn init(core: Core, flags: (View, bool)) -> (Self, Task<Message>) {
        let (start_view, stress) = flags;
        let store = SettingsStore::discover();
        let settings = store.load();
        let interval = Duration::from_millis(settings.interval_ms.max(200));
        let mut app = HWall {
            core,
            sampler: HWall::start_sampler(
                Duration::from_secs(settings.rediscover_seconds.max(30)),
                Duration::from_secs(settings.health_interval_seconds.max(60)),
            ),
            snapshot: Snapshot::new(),
            statistics: SnapshotStatistics::new(),
            settings,
            store,
            rows: Vec::new(),
            device_names: std::collections::HashMap::new(),
            values: std::collections::HashMap::new(),
            limits: std::collections::HashMap::new(),
            device_limits: std::collections::HashMap::new(),
            row_tops: Vec::new(),
            list_height: 0.0,
            scroll_y: 0.0,
            viewport_h: 0.0,
            inventory: None,
            fans: None,
            fans_at: None,
            fans_busy: None,
            fans_error: None,
            fan_edit: None,
            view: start_view,
            interval,
            last_update: None,
            paused: false,
            stress,
            stress_scroll: std::env::args().any(|arg| arg == "--stress-scroll"),
            stress_ticks: 0,
        };
        app.core.window.show_headerbar = true;
        app.rebuild();
        app.set_header_title("Hardware".to_owned());
        (app, Task::none())
    }

    fn update(&mut self, message: Self::Message) -> Task<Self::Message> {
        match message {
            Message::Tick => {
                self.pump();
                return self.maybe_load_fans();
            }
            Message::StressTick => {
                // --stress-repaint: same message rate, no scrolling at all, so the
                // only cost measured is view + layout + raster.
                if !self.stress_scroll {
                    self.stress_ticks = self.stress_ticks.wrapping_add(1);
                    return Task::none();
                }
                // Sweep down for a second, then up: close to "fast scrolling".
                self.stress_ticks = self.stress_ticks.wrapping_add(1);
                let going_down = (self.stress_ticks / 60) % 2 == 0;
                let delta = if going_down { 90.0 } else { -90.0 };
                return cosmic::iced::widget::scrollable::scroll_by::<cosmic::Action<Message>>(
                    cosmic::iced::id::Id::new(LIST_ID),
                    cosmic::iced::widget::scrollable::AbsoluteOffset { x: 0.0, y: delta },
                );
            }
            Message::Scrolled(viewport) => {
                self.scroll_y = viewport.absolute_offset().y;
                self.viewport_h = viewport.bounds().height;
            }
            Message::SetView(view) => {
                if view != self.view {
                    self.view = view;
                    self.rebuild();
                }
            }
            Message::TogglePause => {
                self.paused = !self.paused;
            }
            Message::ClearStatistics => {
                self.statistics.clear();
                self.rebuild();
            }
            Message::SetWarning(key, value) => {
                let rule = self.settings.sensor_alerts.entry(key).or_default();
                rule.warning_above = Some(value);
                self.persist();
            }
            Message::SetCritical(key, value) => {
                let rule = self.settings.sensor_alerts.entry(key).or_default();
                rule.critical_above = Some(value);
                self.persist();
            }
            Message::ClearRule(key) => {
                self.settings.sensor_alerts.remove(&key);
                self.persist();
            }
            Message::FansLoaded(status) => self.fans = Some(status),
            Message::SetFanProfile(name) => {
                return self.fan_command(fans::profile_argv(name), "Changing the profile...")
            }
            Message::DetectFans => {
                return self.fan_command(
                    fans::detect_argv(),
                    "Detecting fans: each spins up for a few seconds...",
                )
            }
            Message::EditFan(id) => {
                self.fans_error = None;
                self.fan_edit = id.and_then(|id| {
                    let status = self.fans.as_ref()?.as_ref().ok()?;
                    let channel = status.channels.iter().find(|c| c.id == id)?;
                    Some(FanEdit::new(channel))
                });
            }
            Message::EditName(text) => {
                if let Some(edit) = &mut self.fan_edit {
                    edit.name = text;
                }
            }
            Message::EditRole(i) => {
                if let Some(edit) = &mut self.fan_edit {
                    edit.role = i;
                }
            }
            Message::EditFollow(i) => {
                if let Some(edit) = &mut self.fan_edit {
                    edit.follow = i;
                }
            }
            Message::EditPoint(i, temp, text) => {
                if let Some(edit) = &mut self.fan_edit {
                    let text: String = text.chars().filter(char::is_ascii_digit).take(3).collect();
                    if let Some(point) = edit.points.get_mut(i) {
                        if temp {
                            point.0 = text;
                        } else {
                            point.1 = text;
                        }
                        edit.own = true;
                        edit.curve_dirty = true;
                    }
                }
            }
            Message::AddPoint => {
                if let Some(edit) = &mut self.fan_edit {
                    if edit.points.len() < edit.max_points.min(8) {
                        // a point halfway along the last segment, or before the end
                        let at = edit.points.len().saturating_sub(1);
                        edit.points.insert(at, (String::new(), String::new()));
                        edit.own = true;
                        edit.curve_dirty = true;
                    }
                }
            }
            Message::RemovePoint(i) => {
                if let Some(edit) = &mut self.fan_edit {
                    if edit.points.len() > 2 {
                        edit.points.remove(i);
                        edit.own = true;
                        edit.curve_dirty = true;
                    }
                }
            }
            Message::ProfileCurve => {
                if let Some(edit) = &mut self.fan_edit {
                    edit.own = false;
                    edit.curve_dirty = true;
                }
            }
            Message::SaveFan => {
                let Some(edit) = &self.fan_edit else {
                    return Task::none();
                };
                match edit.commands() {
                    Ok(commands) if commands.is_empty() => self.fan_edit = None,
                    Ok(commands) => {
                        self.fan_edit = None;
                        return self.fan_commands(commands, "Saving...");
                    }
                    Err(why) => self.fans_error = Some(why),
                }
            }
            Message::FansDone(result) => {
                self.fans_busy = None;
                self.fans_error = result.err();
                self.fans_at = None;
                return self.maybe_load_fans();
            }
        }
        Task::none()
    }

    fn subscription(&self) -> Subscription<Message> {
        let base = cosmic::iced::time::every(TICK).map(|_| Message::Tick);
        if self.stress {
            Subscription::batch([
                base,
                cosmic::iced::time::every(Duration::from_millis(16)).map(|_| Message::StressTick),
            ])
        } else {
            base
        }
    }

    fn header_start(&self) -> Vec<Element<'_, Message>> {
        vec![
            widget::button::standard("Sensors")
                .on_press(Message::SetView(View::Sensors))
                .into(),
            widget::button::standard("Hardware")
                .on_press(Message::SetView(View::Hardware))
                .into(),
            widget::button::standard("Limits")
                .on_press(Message::SetView(View::Limits))
                .into(),
            widget::button::standard("Fans")
                .on_press(Message::SetView(View::Fans))
                .into(),
        ]
    }

    fn view(&self) -> Element<'_, Message> {
        let (state, detail) = self.status_text();
        let status = widget::row::with_capacity(4)
            .push(widget::text::caption(state).width(Length::Fill))
            .push(widget::text::caption(detail).width(Length::Fill))
            .push(
                widget::button::standard(if self.paused { "Resume" } else { "Pause" })
                    .on_press(Message::TogglePause),
            )
            .push(widget::button::standard("Reset stats").on_press(Message::ClearStatistics))
            .spacing(12)
            .padding(10)
            .width(Length::Fill);

        let body = match self.view {
            View::Sensors => self.sensors_view(),
            View::Hardware => self.hardware_view(),
            View::Limits => self.limits_view(),
            View::Fans => self.fans_view(),
        };

        widget::column::with_capacity(2)
            .push(body)
            .push(status)
            .height(Length::Fill)
            .width(Length::Fill)
            .into()
    }
}

fn accent() -> Color {
    theme::active().cosmic().accent.base.into()
}

fn dimmed() -> Color {
    let mut color = accent();
    color.a = 0.55;
    color
}

fn shorten(text: &str, max: usize) -> String {
    if text.chars().count() <= max {
        return text.to_owned();
    }
    let mut out: String = text.chars().take(max.saturating_sub(1)).collect();
    out.push('…');
    out
}

fn parse_hex(hex: &str) -> Color {
    let hex = hex.trim_start_matches('#');
    let value = u32::from_str_radix(hex, 16).unwrap_or(0xFF_FF_FF);
    Color::from_rgb8(
        ((value >> 16) & 0xFF) as u8,
        ((value >> 8) & 0xFF) as u8,
        (value & 0xFF) as u8,
    )
}

/// Width of one numeric column. Fixed, so digits line up down the page.
const NUM: f32 = 94.0;
/// Width of the sensor-name column. Fixed rather than Fill so the numeric block
/// sits right after the names instead of drifting to the far edge of the window.
const LABEL_W: f32 = 332.0;
/// Room for the scrollbar so it cannot overlap the last column.
const SCROLLBAR_W: f32 = 14.0;
/// The list is virtualized, so every row must be this tall.
const ROW_H: f32 = 20.0;
const LIST_ID: &str = "hwall-sensor-list";

/// Fixed height per row kind. Virtualization depends on these being exact.
const fn row_height(kind: RowKind) -> f32 {
    match kind {
        RowKind::Device => 26.0,
        RowKind::Header => 20.0,
        RowKind::Sensor => 18.0,
    }
}

fn col_heading(text: &str) -> Element<'_, Message> {
    widget::text::caption_heading(text.to_owned())
        .size(11.0)
        .line_height(LineHeight::Absolute(Pixels(16.0)))
        .align_x(Alignment::End)
        .width(Length::Fixed(NUM))
        .class(theme::Text::Color(dimmed()))
        .into()
}

fn num_cell(text: String, color: Option<Color>, dimmed_row: bool) -> Element<'static, Message> {
    let base = widget::text::monotext(text)
        .size(12.0)
        .line_height(LineHeight::Absolute(Pixels(16.0)))
        .align_x(Alignment::End)
        .width(Length::Fixed(NUM));
    let cell = match color {
        Some(color) => base.class(theme::Text::Color(color)),
        None if dimmed_row => base.class(theme::Text::Color(dimmed())),
        None => base,
    };
    cell.into()
}

fn warn() -> Color {
    Color::from_rgb(0.91, 0.62, 0.13)
}

/// The theme colour for a [`Heat`] level (red near the limit, amber close to it).
fn heat(value: Option<f64>, limit: Option<f64>) -> Option<Color> {
    tjmax::heat(value, limit).map(|level| match level {
        Heat::Hot => Color::from_rgb(0.88, 0.29, 0.24),
        Heat::Warm => warn(),
    })
}
