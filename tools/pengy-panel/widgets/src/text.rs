//! The [`Text`] widget: a literal or templated text label.
//!
//! Two related widgets live here:
//!
//! * [`Text`] — a fixed, literal label (e.g. the distribution name on the left
//!   of the bar). Its render is constant.
//! * [`Template`] — a label whose `{{var}}` placeholders are substituted from a
//!   map the caller (the backend, or a poll-based updater) supplies on each
//!   tick. This is the seam for labels that change — e.g. a "battery {{pct}}%"
//!   label fed by the backend.
//!
//! Both are *state-fed* in the sense that their visible text is data held in the
//! widget (a template plus, for `Template`, a supplied variable map), not read
//! from the [`crate::source::DataSource`] — so both are trivially
//! headless-testable.

use crate::source::DataSource;
use crate::widget::{Section, Style, TextRun, Widget, WidgetRender};

/// A literal text label.
#[derive(Debug, Clone)]
pub struct Text {
    section: Section,
    text: String,
    style: Style,
}

impl Text {
    /// Build a normal-style literal label.
    pub fn new(section: Section, text: impl Into<String>) -> Self {
        Text {
            section,
            text: text.into(),
            style: Style::Normal,
        }
    }

    /// Move the label to a different section.
    pub fn with_section(mut self, section: Section) -> Self {
        self.section = section;
        self
    }

    /// Set the style tag (mapped to a font/color by the backend).
    pub fn with_style(mut self, style: Style) -> Self {
        self.style = style;
        self
    }

    /// The literal text this label renders.
    pub fn text(&self) -> &str {
        &self.text
    }
}

impl Widget for Text {
    fn id(&self) -> &str {
        "text"
    }

    fn section(&self) -> Section {
        self.section
    }

    fn update(&mut self, _source: &dyn DataSource) -> WidgetRender {
        WidgetRender::new(self.section, vec![TextRun::new(self.text.clone(), self.style)])
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }

    fn as_any_mut(&mut self) -> &mut dyn std::any::Any {
        self
    }
}

/// A label whose `{{var}}` placeholders are filled from a variable map.
///
/// The widget stores the `template`; a [`Template::set_vars`] (backend channel)
/// or a per-tick [`Template::render_with`] supplies the values. Missing keys
/// render as an empty string, so a label degrades to its literal parts rather
/// than erroring.
#[derive(Debug, Clone)]
pub struct Template {
    section: Section,
    template: String,
    style: Style,
    vars: std::collections::BTreeMap<String, String>,
}

impl Template {
    /// Build a template label (initially with no variables).
    pub fn new(section: Section, template: impl Into<String>) -> Self {
        Template {
            section,
            template: template.into(),
            style: Style::Normal,
            vars: std::collections::BTreeMap::new(),
        }
    }

    /// Move the label to a different section.
    pub fn with_section(mut self, section: Section) -> Self {
        self.section = section;
        self
    }

    /// Set the style tag.
    pub fn with_style(mut self, style: Style) -> Self {
        self.style = style;
        self
    }

    /// The raw template string, e.g. `"battery {{pct}}%"`.
    pub fn template(&self) -> &str {
        &self.template
    }

    /// **Backend channel:** replace the variables used on the next tick.
    pub fn set_vars(&mut self, vars: impl IntoIterator<Item = (String, String)>) {
        self.vars.clear();
        for (k, v) in vars {
            self.vars.insert(k, v);
        }
    }

    /// Substitute `{{name}}` occurrences in `template` using `vars`.
    ///
    /// A placeholder is exactly `{{` + `name` + `}}` where `name` is
    /// non-empty. A key absent from `vars` is replaced with the empty string.
    /// `{{`/`}}` with no valid name are left verbatim.
    pub fn render(template: &str, vars: &std::collections::BTreeMap<String, String>) -> String {
        let mut out = String::with_capacity(template.len());
        let bytes = template.as_bytes();
        let mut i = 0;
        while i < bytes.len() {
            if bytes[i] == b'{' && i + 1 < bytes.len() && bytes[i + 1] == b'{' {
                // Candidate `{{`; look for the matching `}}`.
                let after_open = i + 2;
                if let Some(close_rel) =
                    template[after_open..].find("}}")
                {
                    let name_start = after_open;
                    let name_end = after_open + close_rel;
                    let name = &template[name_start..name_end];
                    if !name.is_empty() {
                        let value = vars.get(name).map(String::as_str).unwrap_or("");
                        out.push_str(value);
                        i = name_end + 2; // skip past the closing `}}`
                        continue;
                    }
                }
                // Not a valid placeholder: emit the first `{` literally and move on.
                out.push('{');
                i += 1;
                continue;
            }
            let c = template[i..].chars().next().unwrap();
            out.push(c);
            i += c.len_utf8();
        }
        out
    }
}

impl Widget for Template {
    fn id(&self) -> &str {
        "template"
    }

    fn section(&self) -> Section {
        self.section
    }

    fn update(&mut self, _source: &dyn DataSource) -> WidgetRender {
        let text = Self::render(&self.template, &self.vars);
        WidgetRender::new(self.section, vec![TextRun::new(text, self.style)])
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }

    fn as_any_mut(&mut self) -> &mut dyn std::any::Any {
        self
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::MockSource;

    fn src() -> MockSource {
        MockSource::default()
    }

    // ---- Text ----

    #[test]
    fn text_reports_id_section_and_text() {
        let t = Text::new(Section::Left, "pengy");
        assert_eq!(t.id(), "text");
        assert_eq!(t.section(), Section::Left);
        assert_eq!(t.text(), "pengy");
    }

    #[test]
    fn text_renders_its_literal() {
        let mut t = Text::new(Section::Left, "pengy");
        let r = t.update(&src());
        assert_eq!(r.runs()[0].text, "pengy");
        assert_eq!(r.runs()[0].style, Style::Normal);
        assert_eq!(r.width_cells(), 5);
    }

    #[test]
    fn text_style_and_section_are_configurable() {
        let mut t = Text::new(Section::Center, "Hi").with_style(Style::Emphasized);
        assert_eq!(t.update(&src()).runs()[0].style, Style::Emphasized);
        assert_eq!(t.with_section(Section::Right).section(), Section::Right);
    }

    #[test]
    fn text_is_constant_across_ticks_and_sources() {
        let mut a = Text::new(Section::Left, "hi");
        let mut b = Text::new(Section::Left, "hi");
        assert_eq!(a.update(&src()), b.update(&src()));
        // A "noisy" source changes nothing for a literal label.
        let mut noisy = MockSource::default();
        noisy.now = Some(42);
        assert_eq!(a.update(&noisy), a.update(&src()));
    }

    // ---- Template substitution (pure function) ----

    fn vars(pairs: &[(&str, &str)]) -> std::collections::BTreeMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    #[test]
    fn template_substitutes_a_single_var() {
        assert_eq!(
            Template::render("battery {{pct}}%", &vars(&[("pct", "63")])),
            "battery 63%"
        );
    }

    #[test]
    fn template_substitutes_multiple_and_repeated_vars() {
        assert_eq!(
            Template::render(
                "{{a}}-{{b}}-{{a}}",
                &vars(&[("a", "x"), ("b", "y")])
            ),
            "x-y-x"
        );
    }

    #[test]
    fn template_missing_var_becomes_empty() {
        assert_eq!(
            Template::render("cpu {{cpu}} mem {{mem}}", &vars(&[("cpu", "41%")])),
            "cpu 41% mem "
        );
    }

    #[test]
    fn template_no_placeholders_is_identity() {
        assert_eq!(Template::render("hello", &vars(&[])), "hello");
    }

    #[test]
    fn template_literal_percent_is_not_a_placeholder() {
        assert_eq!(
            Template::render("100% done", &vars(&[])),
            "100% done"
        );
    }

    #[test]
    fn template_adjacent_placeholders() {
        assert_eq!(
            Template::render("{{a}}{{b}}", &vars(&[("a", "1"), ("b", "2")])),
            "12"
        );
    }

    #[test]
    fn template_empty_name_is_left_verbatim() {
        // `{{}}` has an empty name → not a placeholder, left as-is.
        assert_eq!(Template::render("a{{}}b", &vars(&[])), "a{{}}b");
    }

    #[test]
    fn template_unclosed_brace_is_left_verbatim() {
        // A lone `{{` with no closing `}}` is emitted literally.
        assert_eq!(Template::render("x{{y", &vars(&[("y", "z")])), "x{{y");
        // A single brace is untouched.
        assert_eq!(Template::render("a{b}c", &vars(&[])), "a{b}c");
    }

    #[test]
    fn template_empty_value_substitutes_to_empty() {
        assert_eq!(
            Template::render("[{{v}}]", &vars(&[("v", "")])),
            "[]"
        );
    }

    // ---- Template as a widget ----

    #[test]
    fn template_widget_uses_set_vars_backend_channel() {
        let mut t = Template::new(Section::Right, "battery {{pct}}%");
        assert_eq!(t.template(), "battery {{pct}}%");
        // Before any vars are set, the placeholder is empty.
        assert_eq!(t.update(&src()).runs()[0].text, "battery %");
        // The backend pushes the value.
        t.set_vars([("pct".to_string(), "87".to_string())]);
        assert_eq!(t.update(&src()).runs()[0].text, "battery 87%");
    }

    #[test]
    fn template_widget_set_vars_replaces_previous_values() {
        let mut t = Template::new(Section::Right, "{{a}}");
        t.set_vars([("a".to_string(), "1".to_string())]);
        assert_eq!(t.update(&src()).runs()[0].text, "1");
        t.set_vars([("b".to_string(), "2".to_string())]);
        // The old "a" is gone; "{{a}}" now substitutes empty.
        assert_eq!(t.update(&src()).runs()[0].text, "");
    }

    #[test]
    fn template_widget_style_and_section() {
        let mut t = Template::new(Section::Center, "x {{y}}")
            .with_style(Style::Warning)
            .with_section(Section::Left);
        let r = t.update(&src());
        assert_eq!(r.section(), Section::Left);
        assert_eq!(r.runs()[0].style, Style::Warning);
    }

    #[test]
    fn template_reports_id() {
        let t = Template::new(Section::Left, "hi");
        assert_eq!(t.id(), "template");
    }
}
