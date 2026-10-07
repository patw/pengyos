//! Build the panel's widget list from a validated [`crate::PanelConfig`].
//!
//! This is the `config -> Vec<Box<dyn Widget>>` step of the pipeline: the
//! config model (task 5) is pure data; a [`crate::Panel`] (task 6) owns a
//! live widget list; this module bridges the two. It lives in the zero-dep
//! core because it is pure construction — no parsing (the wire layer, in the
//! binary crate, has already applied defaults and validated) and no rendering.
//!
//! A config that fails [`PanelConfig::validate`](crate::PanelConfig::validate)
//! must not reach this function; the builder assumes the invariants
//! validation enforces (e.g. workspace counts in `1..=32`).

use crate::config::WidgetConfig;
use crate::{
    Battery, Clock, PanelConfig, Template, Text, Widget, Workspaces,
};

/// Build the panel's widget list from a validated config, in file order.
///
/// Each built widget is a fresh instance: state-fed widgets start with their
/// initial state (all-empty workspaces, no template variables), which a
/// backend later drives through the `Panel::widget_mut` channel.
pub fn build_widgets(config: &PanelConfig) -> Vec<Box<dyn Widget>> {
    config.widgets.iter().map(build_widget).collect()
}

/// Build the single widget described by one `[[widgets]]` entry.
pub fn build_widget(widget_config: &WidgetConfig) -> Box<dyn Widget> {
    match widget_config {
        WidgetConfig::Clock { section, format } => {
            Box::new(Clock::new(*section, format.clone()))
        }
        WidgetConfig::Battery {
            section,
            supply,
            low,
            critical,
        } => Box::new(
            Battery::new(*section, supply.clone()).with_thresholds(*low, *critical),
        ),
        WidgetConfig::Workspaces { section, count } => {
            Box::new(Workspaces::new(*section, *count))
        }
        WidgetConfig::Text { section, text } => Box::new(Text::new(*section, text.clone())),
        WidgetConfig::Template { section, text } => {
            Box::new(Template::new(*section, text.clone()))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::WidgetConfig;
    use crate::text::Template;
    use crate::Section;

    fn config_with(widgets: Vec<WidgetConfig>) -> PanelConfig {
        let mut config = PanelConfig::default();
        config.widgets = widgets;
        config
    }

    /// Downcast a built widget through the `as_any` channel to check its
    /// concrete type — the same channel the backend uses.
    fn downcast<T: Widget + 'static>(w: &dyn Widget) -> Option<&T> {
        w.as_any().downcast_ref::<T>()
    }

    #[test]
    fn empty_config_builds_an_empty_panel() {
        let widgets = build_widgets(&PanelConfig::default());
        assert!(widgets.is_empty());
    }

    #[test]
    fn all_five_kinds_build_the_right_concrete_types() {
        let config = config_with(vec![
            WidgetConfig::clock(Section::Center, Some("%H:%M".to_string())),
            WidgetConfig::battery(Section::Right, Some("BAT1".to_string()), Some(30), Some(5)),
            WidgetConfig::workspaces(Section::Left, Some(5)),
            WidgetConfig::text(Section::Left, "hello".to_string()),
            WidgetConfig::template(Section::Right, "{{var}}".to_string()),
        ]);
        let widgets = build_widgets(&config);
        assert_eq!(widgets.len(), 5);

        let clock = downcast::<Clock>(widgets[0].as_ref()).expect("widget 0 is a Clock");
        assert_eq!(clock.format(), "%H:%M");
        assert_eq!(clock.section(), Section::Center);

        let battery = downcast::<Battery>(widgets[1].as_ref()).expect("widget 1 is a Battery");
        assert_eq!(battery.supply(), "BAT1");
        assert_eq!(battery.section(), Section::Right);

        let ws = downcast::<Workspaces>(widgets[2].as_ref()).expect("widget 2 is Workspaces");
        assert_eq!(ws.count(), 5);
        assert_eq!(ws.section(), Section::Left);

        let text = downcast::<Text>(widgets[3].as_ref()).expect("widget 3 is Text");
        assert_eq!(text.text(), "hello");

        let template = downcast::<Template>(widgets[4].as_ref())
            .expect("widget 4 is a Template");
        assert_eq!(template.template(), "{{var}}");
        // State-fed widgets start at their initial state (no variables set).
        assert_eq!(Template::render("{{var}}", &std::collections::BTreeMap::new()), "");
    }

    #[test]
    fn build_preserves_config_order_and_sections() {
        let config = config_with(vec![
            WidgetConfig::text(Section::Right, "b".to_string()),
            WidgetConfig::text(Section::Left, "a".to_string()),
            WidgetConfig::text(Section::Center, "c".to_string()),
        ]);
        let widgets = build_widgets(&config);
        assert_eq!(widgets.iter().map(|w| w.section()).collect::<Vec<_>>(),
            vec![Section::Right, Section::Left, Section::Center]);
    }

    #[test]
    fn built_widgets_start_at_their_initial_state() {
        let config = config_with(vec![
            WidgetConfig::workspaces(Section::Left, Some(3)),
            WidgetConfig::template(Section::Right, "h={{name}}".to_string()),
        ]);
        let mut widgets = build_widgets(&config);

        // Workspaces: all-empty initial state renders as plain numbers.
        let ws = downcast::<Workspaces>(widgets[0].as_ref()).unwrap();
        assert!(ws.states().iter().all(|s| matches!(s, crate::WorkspaceState::Empty)));

        // Template: no variables set yet, so `{{name}}` substitutes empty.
        let mut src = crate::testutil::MockSource::default();
        let render = widgets.get_mut(1).unwrap().update(&mut src);
        assert_eq!(render.runs()[0].text, "h=");
    }

    #[test]
    fn build_is_pure_and_repeatable_for_the_same_config() {
        let config = config_with(vec![
            WidgetConfig::clock(Section::Left, None),
            WidgetConfig::battery(Section::Right, None, None, None),
        ]);
        let mut a = build_widgets(&config);
        let mut b = build_widgets(&config);
        assert_eq!(a.len(), b.len());
        for (wa, wb) in a.iter_mut().zip(b.iter_mut()) {
            // Same ids, sections, and renders for the same (empty) source.
            let src = crate::testutil::MockSource::default();
            assert_eq!(wa.id(), wb.id());
            assert_eq!(wa.section(), wb.section());
            assert_eq!(wa.update(&src), wb.update(&src));
        }
    }

    #[test]
    fn default_applied_entries_build_with_their_documented_defaults() {
        // The wire layer applies defaults before validation; the builder
        // consumes whatever is present. `None`-constructed entries carry the
        // documented defaults (see `widgets::config`).
        let config = config_with(vec![
            WidgetConfig::clock(Section::Center, None),
            WidgetConfig::battery(Section::Right, None, None, None),
            WidgetConfig::workspaces(Section::Left, None),
        ]);
        config.validate().unwrap();
        let widgets = build_widgets(&config);

        let clock = downcast::<Clock>(widgets[0].as_ref()).unwrap();
        assert_eq!(clock.format(), "%H:%M"); // DEFAULT_CLOCK_FORMAT
        let battery = downcast::<Battery>(widgets[1].as_ref()).unwrap();
        assert_eq!(battery.supply(), "BAT0"); // DEFAULT_BATTERY_SUPPLY
        let ws = downcast::<Workspaces>(widgets[2].as_ref()).unwrap();
        assert_eq!(ws.count(), 3); // DEFAULT_WORKSPACES_COUNT
    }
}
