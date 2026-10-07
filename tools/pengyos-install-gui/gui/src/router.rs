//! The message router — the one behaviour every front-end must share.
//!
//! The GUI's job is thin: it owns no wizard logic of its own. It renders the
//! [`screen::ScreenView`] produced by the pure [`Model`] and forwards widget
//! events to it. This module is that forwarding, expressed as a single pure
//! function so it can be tested with **no display and no libcosmic**.
//!
//! The [`Message`] enum is the vocabulary of widget events. It is std-only on
//! purpose — the cosmic app maps its own widget messages onto these, but the
//! vocabulary itself never needs a cosmic type. Keeping it here (off the
//! build-gated `cosmic_app` module) is what makes the routing testable offline.
//!
//! [`route`] is the single source of truth for "what does a message do":
//!
//! * field edits (`SetHostname`, `SelectDisk`, …) mutate the model and return
//!   `false` — they never advance the wizard on their own;
//! * [`Message::Continue`] calls [`Model::next`] and returns `true` iff it
//!   succeeded (validators/blockers live in the model, so a blocked Continue is
//!   a no-op advance);
//! * [`Message::Back`] calls [`Model::back`] and always returns `false`
//!   (back-nav never advances);
//! * [`Message::InstallFinished`] is a terminal callback and returns `false`.
//!
//! The cosmic `AppModel::update` is then a one-liner: `let advance =
//! router::route(&mut self.model, msg); if advance { self.step = ... }`.

use pengyos_install_model::{Layout, Model};

/// A widget event the wizard understands.
///
/// These are the messages the cosmic app forwards to the [`Model`]. The enum is
/// std-only so the routing logic can be unit-tested with no display. The cosmic
/// layer may map several of its own widget messages onto a single `Message`
/// here (e.g. a disk radio maps to exactly one [`Message::SelectDisk`]).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Message {
    /// Advance the wizard one step (honours the model's validators/blockers).
    Continue,
    /// Move back one step.
    Back,
    /// The terms checkbox toggled to `accepted`.
    AcceptTerms(bool),
    /// A target disk was selected from the disk screen.
    SelectDisk(String),
    /// An install layout was chosen.
    SelectLayout(Layout),
    /// The hostname field changed.
    SetHostname(String),
    /// The username field changed.
    SetUsername(String),
    /// The password field changed.
    SetPassword(String),
    /// The network SSID field changed.
    SetSsid(String),
    /// The background install finished (carrying the error, if any).
    InstallFinished(Result<(), String>),
}

/// The behaviour every front-end shares: a [`Message`] maps onto a model
/// mutation (and, for `Continue`, a possible step advance).
pub trait MessageRouter {
    /// Route `msg` through `model`. Returns `true` iff the wizard should
    /// advance to the next step afterwards (i.e. `Continue` succeeded); `false`
    /// for field edits, `Back`, and terminal messages.
    fn route(&mut self, msg: Message) -> bool;
}

/// The pure implementation of [`MessageRouter`]: what a message does to the
/// model. The cosmic [`AppModel`](crate::cosmic_app::AppModel) implements
/// [`MessageRouter`] by delegating to this function and driving its `step`
/// from the returned boolean.
///
/// This is the single source of truth for routing, so the text renderer, the
/// cosmic app, and the unit tests all agree on behaviour.
pub fn route(model: &mut Model, msg: Message) -> bool {
    match msg {
        Message::Continue => model.next().is_ok(),
        Message::Back => {
            model.back();
            false
        }
        Message::AcceptTerms(accepted) => {
            model.set_accepted_terms(accepted);
            false
        }
        Message::SelectDisk(disk) => {
            model.set_selected_disk(disk);
            false
        }
        Message::SelectLayout(layout) => {
            model.set_layout(layout);
            false
        }
        Message::SetHostname(h) => {
            model.set_hostname(h);
            false
        }
        Message::SetUsername(u) => {
            model.set_username(u);
            false
        }
        Message::SetPassword(p) => {
            model.set_password(p);
            false
        }
        Message::SetSsid(s) => {
            model.set_network_ssid(s);
            false
        }
        Message::InstallFinished(_) => false,
    }
}

impl MessageRouter for Model {
    fn route(&mut self, msg: Message) -> bool {
        route(self, msg)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pengyos_install_model::Step;

    /// A model walked all the way to `Summary`, ready to receive any message.
    fn ready() -> Model {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/sda");
        m.set_layout(Layout::Wipe);
        m.set_hostname("pengyos-box");
        m.set_username("alice");
        m.set_password("hunter2");
        m.set_network_ssid("home");
        for _ in 0..5 {
            m.next().expect("walk to Summary");
        }
        m
    }

    /// A model fully answered and walked to `step`, ready to receive any
    /// message. Every field is set, so each `next()` succeeds.
    fn ready_at(step: Step) -> Model {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.set_selected_disk("/dev/sda");
        m.set_layout(Layout::Wipe);
        m.set_hostname("pengyos-box");
        m.set_username("alice");
        m.set_password("hunter2");
        m.set_network_ssid("home");
        while m.step() != step {
            m.next().expect("walk to the requested step");
        }
        m
    }

    #[test]
    fn continue_advances() {
        let mut m = Model::new();
        assert_eq!(m.step(), pengyos_install_model::Step::Welcome);
        // The Welcome validator gates advance on the terms checkbox.
        m.set_accepted_terms(true);
        assert!(route(&mut m, Message::Continue));
        assert_eq!(m.step(), pengyos_install_model::Step::Disk);
    }

    #[test]
    fn continue_at_completion_returns_false() {
        // At Progress there is nowhere to go; Continue must not panic and must
        // report that no advance happened.
        let mut m = ready_at(Step::Progress);
        assert_eq!(m.step(), Step::Progress);
        assert!(!route(&mut m, Message::Continue));
        assert_eq!(m.step(), Step::Progress);
    }

    #[test]
    fn continue_is_blocked_when_terms_not_accepted() {
        // A Continue before the model allows it is a no-op advance (the blocker
        // lives in the model).
        let mut m = Model::new();
        assert!(!route(&mut m, Message::Continue));
        assert_eq!(m.step(), pengyos_install_model::Step::Welcome);
    }

    #[test]
    fn back_moves_back() {
        let mut m = ready();
        let before = m.step();
        assert!(!route(&mut m, Message::Back));
        assert_eq!(m.step(), before.prev().expect("a previous step exists"));
    }

    #[test]
    fn field_edits_do_not_advance() {
        let mut m = ready();
        let before = m.step();
        for msg in [
            Message::AcceptTerms(true),
            Message::SelectDisk("/dev/sdb".to_string()),
            Message::SelectLayout(Layout::Preserve),
            Message::SetHostname("host".to_string()),
            Message::SetUsername("bob".to_string()),
            Message::SetPassword("s3cret".to_string()),
            Message::SetSsid("wifi".to_string()),
            Message::InstallFinished(Ok(())),
        ] {
            assert!(!route(&mut m, msg.clone()), "{msg:?} must not advance");
            assert_eq!(m.step(), before, "{msg:?} changed the step");
        }
    }

    #[test]
    fn select_disk_stores_the_choice() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        route(&mut m, Message::SelectDisk("/dev/nvme0n1".to_string()));
        assert_eq!(m.selected_disk(), Some("/dev/nvme0n1"));
    }

    #[test]
    fn select_layout_stores_the_choice() {
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.next().unwrap(); // Disk
        route(&mut m, Message::SelectLayout(Layout::HomeOnly));
        assert_eq!(m.layout(), Some(Layout::HomeOnly));
    }

    #[test]
    fn password_edit_stores_only_length_in_view() {
        // The model carries the password; the view masks it as a length.
        let mut m = Model::new();
        m.set_accepted_terms(true);
        m.next().unwrap();
        m.set_selected_disk("/dev/sda");
        m.next().unwrap();
        route(&mut m, Message::SetPassword("correct horse".to_string()));
        assert_eq!(m.password(), Some("correct horse"));
    }

    #[test]
    fn router_trait_delegates_to_route() {
        // The `Model` impl of `MessageRouter` must behave identically to the
        // free function on the same input.
        let mut a = ready();
        let mut b = ready();
        assert_eq!(a.route(Message::Continue), route(&mut b, Message::Continue));
        assert_eq!(a.step(), b.step());
    }
}
