//! The Fans panel applet (`pengyos-hwmon --applet`): a fan icon in the COSMIC
//! panel whose popup switches the fan profile in one click and shows each fan's
//! speed. The full editor is the Hardware Monitor's Fans view, one click away.
//!
//! It lives in this binary, not its own, so it shares the Fans code and the
//! libcosmic build with the window. It is invisible on a machine with no fan
//! controls (laptops already have power profiles in COSMIC's battery applet), so
//! the image can put it on every Desktop's panel.

use crate::fans::{self, FanStatus, Kind};
use cosmic::app::{Core, Task};
use cosmic::iced::{window, Alignment, Length, Subscription};
use cosmic::widget;
use cosmic::Element;
use std::thread;
use std::time::Duration;

const APP_ID: &str = "io.catbee.pengyos.FansApplet";
const ICON: &str = "io.catbee.pengyos.FansApplet-symbolic";
/// Ask pengyos-fans this often while the popup is open, and this often otherwise
/// (only to know whether to show the icon at all).
const OPEN_EVERY: Duration = Duration::from_secs(2);
const CLOSED_EVERY: Duration = Duration::from_secs(60);

pub fn run() -> cosmic::iced::Result {
    cosmic::applet::run::<FansApplet>(())
}

struct FansApplet {
    core: Core,
    popup: Option<window::Id>,
    status: Option<Result<FanStatus, String>>,
    busy: bool,
    error: Option<String>,
}

#[derive(Debug, Clone)]
enum Message {
    TogglePopup,
    Closed(window::Id),
    Refresh,
    Loaded(Result<FanStatus, String>),
    Profile(&'static str),
    Done(Result<(), String>),
    OpenMonitor,
}

fn load() -> Task<Message> {
    cosmic::task::future(async move {
        let status = thread::spawn(fans::query)
            .join()
            .unwrap_or_else(|_| Err("pengyos-fans status crashed".into()));
        Message::Loaded(status)
    })
}

/// Whether there is anything for the applet to do on this machine.
fn shown(status: &Option<Result<FanStatus, String>>) -> bool {
    matches!(status, Some(Ok(s)) if s.kind == Kind::Channels)
}

fn profile_label(name: &str) -> &'static str {
    match name {
        "quiet" => "Quiet",
        "balanced" => "Balanced",
        "full" => "Full speed",
        _ => "As the firmware set it",
    }
}

impl cosmic::Application for FansApplet {
    type Executor = cosmic::executor::Default;
    type Flags = ();
    type Message = Message;

    const APP_ID: &'static str = APP_ID;

    fn core(&self) -> &Core {
        &self.core
    }

    fn core_mut(&mut self) -> &mut Core {
        &mut self.core
    }

    fn init(core: Core, _flags: ()) -> (Self, Task<Message>) {
        let applet = FansApplet {
            core,
            popup: None,
            status: None,
            busy: false,
            error: None,
        };
        (applet, load())
    }

    fn on_close_requested(&self, id: window::Id) -> Option<Message> {
        Some(Message::Closed(id))
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::TogglePopup => {
                if let Some(p) = self.popup.take() {
                    return cosmic::surface::surface_task(cosmic::surface::action::destroy_popup(
                        p,
                    ));
                }
                self.error = None;
                let open = cosmic::surface::surface_task(cosmic::surface::action::app_popup(
                    |_| Default::default(),
                    |app: &mut FansApplet| {
                        let id = window::Id::unique();
                        app.popup = Some(id);
                        app.core.applet.get_popup_settings(
                            app.core.main_window_id().unwrap(),
                            id,
                            None,
                            None,
                            None,
                        )
                    },
                    None,
                ));
                return Task::batch([open, load()]);
            }
            Message::Closed(id) => {
                if self.popup == Some(id) {
                    self.popup = None;
                }
            }
            Message::Refresh => {
                if !self.busy {
                    return load();
                }
            }
            Message::Loaded(status) => self.status = Some(status),
            Message::Profile(name) => {
                if self.busy {
                    return Task::none();
                }
                self.busy = true;
                self.error = None;
                let argv = fans::profile_argv(name);
                return cosmic::task::future(async move {
                    let result = thread::spawn(move || fans::run(&argv))
                        .join()
                        .unwrap_or_else(|_| Err("pengyos-fans crashed".into()));
                    Message::Done(result)
                });
            }
            Message::Done(result) => {
                self.busy = false;
                self.error = result.err();
                return load();
            }
            Message::OpenMonitor => {
                let _ = std::process::Command::new("pengyos-hwmon")
                    .args(["--view", "fans"])
                    .spawn();
                if let Some(p) = self.popup.take() {
                    return cosmic::surface::surface_task(cosmic::surface::action::destroy_popup(
                        p,
                    ));
                }
            }
        }
        Task::none()
    }

    fn subscription(&self) -> Subscription<Message> {
        let every = if self.popup.is_some() {
            OPEN_EVERY
        } else {
            CLOSED_EVERY
        };
        cosmic::iced::time::every(every).map(|_| Message::Refresh)
    }

    fn view(&self) -> Element<'_, Message> {
        if !shown(&self.status) {
            // Nothing to control here: take no room in the panel.
            return widget::Space::new()
                .width(Length::Fixed(0.0))
                .height(Length::Fixed(0.0))
                .into();
        }
        self.core
            .applet
            .icon_button(ICON)
            .on_press_down(Message::TogglePopup)
            .into()
    }

    fn view_window(&self, id: window::Id) -> Element<'_, Message> {
        if self.popup != Some(id) {
            return widget::text("").into();
        }
        let mut col = widget::column::with_capacity(12).padding([8, 0]);
        col = col.push(cosmic::applet::padded_control(widget::text::heading(
            "Fans",
        )));
        match &self.status {
            Some(Ok(status)) if !status.channels.is_empty() => {
                for name in fans::PROFILES {
                    let current = status.profile == name;
                    let mut row = widget::row::with_capacity(2)
                        .spacing(8)
                        .align_y(Alignment::Center)
                        .push(widget::text::body(profile_label(name)).width(Length::Fill));
                    if current {
                        row = row.push(widget::icon::from_name("object-select-symbolic").size(16));
                    }
                    let button = cosmic::applet::menu_button(row);
                    col = col.push(if self.busy || current {
                        button
                    } else {
                        button.on_press(Message::Profile(name))
                    });
                }
                col = col.push(cosmic::applet::padded_control(
                    widget::divider::horizontal::default(),
                ));
                for c in status.channels.iter().filter(|c| c.role == "fan") {
                    let name = if c.name.is_empty() {
                        c.id.clone()
                    } else {
                        c.name.clone()
                    };
                    col = col.push(cosmic::applet::padded_control(
                        widget::row::with_capacity(2)
                            .push(widget::text::body(name).width(Length::Fill))
                            .push(widget::text::body(fans::rpm_text(c))),
                    ));
                }
            }
            Some(Ok(status)) => {
                col = col.push(cosmic::applet::padded_control(widget::text::body(
                    fans::summary(status),
                )));
            }
            Some(Err(why)) => {
                col = col.push(cosmic::applet::padded_control(widget::text::body(
                    why.clone(),
                )));
            }
            None => {
                col = col.push(cosmic::applet::padded_control(widget::text::body(
                    "Reading...",
                )));
            }
        }
        if self.busy {
            col = col.push(cosmic::applet::padded_control(widget::text::caption(
                "Changing...",
            )));
        }
        if let Some(why) = &self.error {
            col = col.push(cosmic::applet::padded_control(widget::text::caption(
                why.clone(),
            )));
        }
        col = col.push(cosmic::applet::padded_control(
            widget::divider::horizontal::default(),
        ));
        col = col.push(
            cosmic::applet::menu_button(widget::text::body("Curves and names..."))
                .on_press(Message::OpenMonitor),
        );
        self.core
            .applet
            .popup_container(col.width(Length::Fixed(300.0)))
            .into()
    }

    fn style(&self) -> Option<cosmic::iced::theme::Style> {
        Some(cosmic::applet::style())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shown_only_where_fans_can_be_controlled() {
        let parse = |kind: &str| {
            Some(fans::parse(&format!(
                r#"{{"kind":"{kind}","profile":"firmware","channels":[],"undetected":[],"platform_profile":null}}"#
            )))
        };
        assert!(shown(&parse("channels")));
        assert!(!shown(&parse("platform")));
        assert!(!shown(&parse("none")));
        assert!(!shown(&None));
        assert!(!shown(&Some(Err("not installed".into()))));
    }
}
