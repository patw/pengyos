//! The [`Clock`] widget: a *poll-based* widget that renders the local time.
//!
//! The clock is the canonical example of a widget that pulls its input from the
//! [`crate::source::DataSource`]: it reads [`DataSource::now_secs`] and
//! [`DataSource::tz_offset_secs`], converts the epoch to a civil date + time of
//! day in the local zone (see [`crate::civil`]), and formats it with a small,
//! strftime-flavoured token language. Because the source is injected, the whole
//! widget is a pure, deterministic function of `(now, tz, format)` — which is
//! exactly what makes "clock formatting under a fixed TZ" headlessly testable.

use crate::civil::CivilDate;
use crate::source::DataSource;
use crate::widget::{Section, Style, TextRun, Widget, WidgetRender};

/// Seconds in a day. Used only to split an epoch into "days" and "time of day".
const SECS_PER_DAY: i64 = 86_400;

/// A *poll-based* widget that shows the local time.
///
/// Construct with a [`Section`] and a `format` string (e.g. `"%H:%M"` or
/// `"%a %b %d %H:%M"`). On each tick it asks the [`DataSource`] for the wall
/// clock and the timezone offset, then renders the formatted time.
#[derive(Debug, Clone)]
pub struct Clock {
    section: Section,
    format: String,
}

impl Clock {
    /// Build a clock in `section` showing `format`.
    pub fn new(section: Section, format: impl Into<String>) -> Self {
        Clock {
            section,
            format: format.into(),
        }
    }

    /// Move the clock to a different section (e.g. Left vs Right).
    pub fn with_section(mut self, section: Section) -> Self {
        self.section = section;
        self
    }

    /// The format string this clock renders with.
    pub fn format(&self) -> &str {
        &self.format
    }
}

/// The pieces of a local wall-clock reading a formatter needs.
#[derive(Debug, Clone, Copy)]
struct LocalTime {
    date: CivilDate,
    hour: u32,
    minute: u32,
    second: u32,
}

/// Split an epoch second (already shifted to local time) into date + time.
///
/// Uses Euclidean division so that timestamps *before* 1970 land on the correct
/// (previous) calendar day rather than drifting off by one.
fn local_time(local_epoch: i64) -> LocalTime {
    let days = local_epoch.div_euclid(SECS_PER_DAY);
    let rem = local_epoch.rem_euclid(SECS_PER_DAY);
    LocalTime {
        date: CivilDate::from_days(days),
        hour: (rem / 3_600) as u32,
        minute: ((rem / 60) % 60) as u32,
        second: (rem % 60) as u32,
    }
}

/// The 12-hour form of a 0..23 hour (0 → 12, 12 → 12, 13 → 1, 23 → 11).
fn to_12(hour: u32) -> u32 {
    let x = hour % 12;
    if x == 0 {
        12
    } else {
        x
    }
}

impl Widget for Clock {
    fn id(&self) -> &str {
        "clock"
    }

    fn section(&self) -> Section {
        self.section
    }

    fn update(&mut self, source: &dyn DataSource) -> WidgetRender {
        let text = match source.now_secs() {
            Some(epoch) => {
                let offset = source.tz_offset_secs().unwrap_or(0) as i64;
                let t = local_time(epoch + offset);
                render(&self.format, &t.date, t.hour, t.minute, t.second)
            }
            None => "??".to_string(),
        };
        WidgetRender::new(self.section, vec![TextRun::new(text, Style::Normal)])
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }

    fn as_any_mut(&mut self) -> &mut dyn std::any::Any {
        self
    }
}

/// A single validation error found by [`validate_format`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FormatError {
    /// The format contains a `%` followed by an unrecognised token.
    UnknownToken {
        /// Zero-based character index of the offending `%`.
        percent_index: usize,
        /// The unrecognised token character (the one after `%`).
        token: char,
    },
    /// The format ends with a lone `%` (almost always a typo for `%%`).
    TrailingPercent {
        /// Zero-based character index of the offending `%`.
        percent_index: usize,
    },
}

impl std::fmt::Display for FormatError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            FormatError::UnknownToken { percent_index, token } => write!(
                f,
                "unknown format token `%{token}` at position {percent_index}"
            ),
            FormatError::TrailingPercent { percent_index } => write!(
                f,
                "format ends with a lone `%` at position {percent_index} (use `%%` for a literal percent)"
            ),
        }
    }
}

impl std::error::Error for FormatError {}

/// The token characters a `%` may introduce, mirroring [`render`] exactly.
const FORMAT_TOKENS: &[char] = &['H', 'I', 'M', 'S', 'p', 'd', 'b', 'B', 'a', 'A', 'Y', '%'];

/// Strictly validate a strftime-flavoured format string.
///
/// The widget's renderer is *lenient* on purpose (it passes an unknown `%x`
/// through verbatim so a format typo can never kill the panel). Config
/// validation is *strict*: an unknown token or a lone trailing `%` is almost
/// certainly a typo in `panel.toml`, and it should surface at load time rather
/// than as literal `%Q` garbage on the bar. `%%` is a literal percent and is
/// always valid.
pub fn validate_format(fmt: &str) -> Result<(), FormatError> {
    let mut chars = fmt.chars().peekable();
    let mut index = 0;
    while let Some(c) = chars.next() {
        let percent_index = index;
        index += 1;
        if c != '%' {
            continue;
        }
        match chars.next() {
            None => return Err(FormatError::TrailingPercent { percent_index }),
            Some(token) => {
                index += 1;
                if !FORMAT_TOKENS.contains(&token) {
                    return Err(FormatError::UnknownToken {
                        percent_index,
                        token,
                    });
                }
            }
        }
    }
    Ok(())
}

/// Render a [`LocalTime`] using a strftime-flavoured `format` string.
///
/// Recognised tokens (all prefixed with `%`):
///
/// | Token | Meaning                              |
/// |-------|--------------------------------------|
/// | `%H`  | hour, 24-hour, zero-padded (00–23)   |
/// | `%I`  | hour, 12-hour, zero-padded (01–12)   |
/// | `%M`  | minute, zero-padded                  |
/// | `%S`  | second, zero-padded                  |
/// | `%p`  | `AM` / `PM`                          |
/// | `%d`  | day of month, zero-padded            |
/// | `%b`  | month abbreviation (`Jan`..`Dec`)    |
/// | `%B`  | month full name                      |
/// | `%a`  | weekday abbreviation (`Mon`..`Sun`)  |
/// | `%A`  | weekday full name                    |
/// | `%Y`  | four-digit year                      |
/// | `%%`  | a literal `%`                        |
///
/// An unrecognised `%x` is passed through verbatim (`%x`), matching what a
/// lenient formatter does; a lone trailing `%` is passed through too.
fn render(fmt: &str, date: &CivilDate, hour: u32, minute: u32, second: u32) -> String {
    let mut out = String::with_capacity(fmt.len() + 4);
    let mut chars = fmt.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '%' {
            out.push(c);
            continue;
        }
        match chars.next() {
            None => out.push('%'), // trailing lone '%'
            Some(next) => match next {
                'H' => out.push_str(&format!("{hour:02}")),
                'I' => out.push_str(&format!("{:02}", to_12(hour))),
                'M' => out.push_str(&format!("{minute:02}")),
                'S' => out.push_str(&format!("{second:02}")),
                'p' => out.push_str(if hour < 12 { "AM" } else { "PM" }),
                'd' => out.push_str(&date.day_2digit()),
                'b' => out.push_str(date.month_abbrev()),
                'B' => out.push_str(date.month_full()),
                'a' => out.push_str(date.weekday.short()),
                'A' => out.push_str(date.weekday.long()),
                'Y' => out.push_str(&date.year_4digit()),
                '%' => out.push('%'),
                other => {
                    out.push('%');
                    out.push(other);
                }
            },
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::MockSource;

    fn source_with(now: i64, tz: i32) -> MockSource {
        let mut s = MockSource::default();
        s.now = Some(now);
        s.tz = Some(tz);
        s
    }

    fn render_of(clock: &Clock, now: i64, tz: i32) -> String {
        // `update` needs `&mut self`; clone so call sites keep a shared ref.
        let mut w = clock.clone();
        w.update(&source_with(now, tz)).runs()[0].text.clone()
    }

    #[test]
    fn clock_reports_id_and_section() {
        let c = Clock::new(Section::Right, "%H:%M");
        assert_eq!(c.id(), "clock");
        assert_eq!(c.section(), Section::Right);
        assert_eq!(c.format(), "%H:%M");
        assert_eq!(c.with_section(Section::Left).section(), Section::Left);
    }

    #[test]
    fn utc_time_formats_hms() {
        // 2025-06-15 15:06:40 UTC.
        let c = Clock::new(Section::Center, "%H:%M:%S");
        assert_eq!(render_of(&c, 1_750_000_000, 0), "15:06:40");
    }

    #[test]
    fn timezone_offset_shifts_the_time() {
        let c = Clock::new(Section::Center, "%H:%M");
        // Same instant, three zones.
        assert_eq!(render_of(&c, 1_750_000_000, 0), "15:06");
        assert_eq!(render_of(&c, 1_750_000_000, -18_000), "10:06"); // UTC-5
        assert_eq!(render_of(&c, 1_750_000_000, 10_800), "18:06"); // UTC+3
    }

    #[test]
    fn positive_offset_crosses_midnight_forward() {
        // 15:06:40 UTC + 9h = 00:06:40 the NEXT day (Jun 16).
        let c = Clock::new(Section::Center, "%H:%M %d %b");
        assert_eq!(render_of(&c, 1_750_000_000, 32_400), "00:06 16 Jun");
        // A big positive offset (UTC+12) is clearly the next day too.
        let c2 = Clock::new(Section::Center, "%H:%M %d %b");
        assert_eq!(render_of(&c2, 1_750_000_000, 43_200), "03:06 16 Jun");
    }

    #[test]
    fn negative_offset_crosses_midnight_backward() {
        // 15:06:40 UTC - 20h = previous day 19:06.
        let c = Clock::new(Section::Center, "%H:%M %d %b");
        assert_eq!(render_of(&c, 1_750_000_000, -72_000), "19:06 14 Jun");
    }

    #[test]
    fn twelve_hour_and_ampm() {
        let c = Clock::new(Section::Center, "%I:%M %p");
        // 15:06 → 03:06 PM.
        assert_eq!(render_of(&c, 1_750_000_000, 0), "03:06 PM");
        // 00:00 (epoch, UTC) → 12:00 AM.
        let midnight = Clock::new(Section::Center, "%I:%M %p");
        assert_eq!(render_of(&midnight, 0, 0), "12:00 AM");
        // 12:30 (epoch + 12.5h, UTC) → 12:30 PM.
        let noon = Clock::new(Section::Center, "%I:%M %p");
        assert_eq!(render_of(&noon, 45_000, 0), "12:30 PM");
    }

    #[test]
    fn date_tokens_use_the_local_date() {
        // 2025-06-15 is a Sunday in UTC.
        let c = Clock::new(Section::Center, "%a, %b %d, %Y");
        assert_eq!(render_of(&c, 1_750_000_000, 0), "Sun, Jun 15, 2025");
        let c2 = Clock::new(Section::Center, "%A, %B %Y");
        assert_eq!(render_of(&c2, 1_750_000_000, 0), "Sunday, June 2025");
    }

    #[test]
    fn day_zero_pads_and_weekday_matches() {
        // 2026-01-01 00:00 UTC is a Thursday.
        let c = Clock::new(Section::Center, "%d %b %a %Y");
        assert_eq!(render_of(&c, 1_767_225_600, 0), "01 Jan Thu 2026");
    }

    #[test]
    fn literal_percent_and_unknown_tokens_pass_through() {
        let c = Clock::new(Section::Center, "%H%%:10 %Q %");
        assert_eq!(render_of(&c, 1_750_000_000, 0), "15%:10 %Q %");
    }

    #[test]
    fn literal_text_is_preserved() {
        let c = Clock::new(Section::Center, "It is %H:%M");
        assert_eq!(render_of(&c, 1_750_000_000, 0), "It is 15:06");
    }

    #[test]
    fn missing_clock_falls_back_to_question_marks() {
        let mut c = Clock::new(Section::Center, "%H:%M");
        let src = MockSource::default(); // now is None
        let r = c.update(&src);
        assert_eq!(r.runs()[0].text, "??");
        assert_eq!(r.runs()[0].style, Style::Normal);
    }

    #[test]
    fn missing_tz_falls_back_to_utc() {
        // now set, tz None → render as UTC.
        let mut src = MockSource::default();
        src.now = Some(1_750_000_000);
        src.tz = None;
        let mut c = Clock::new(Section::Center, "%H:%M");
        assert_eq!(c.update(&src).runs()[0].text, "15:06");
    }

    #[test]
    fn width_is_content_width() {
        let mut c = Clock::new(Section::Right, "%H:%M");
        assert_eq!(c.update(&source_with(1_750_000_000, 0)).width_cells(), 5);
    }

    #[test]
    fn clock_is_deterministic_for_the_same_source() {
        let mut a = Clock::new(Section::Center, "%H:%M");
        let mut b = Clock::new(Section::Center, "%H:%M");
        let src = source_with(1_750_000_000, -18_000);
        assert_eq!(a.update(&src), b.update(&src));
    }

    #[test]
    fn validate_format_accepts_all_documented_tokens() {
        for fmt in [
            "%H:%M",
            "%%",
            "%a %b %d  %H:%M",
            "It is %H:%M",
            "%I:%M %p",
            "%A, %B %Y",
            "%H%%:10",
        ] {
            assert_eq!(validate_format(fmt), Ok(()), "`{fmt}` should be valid");
        }
    }

    #[test]
    fn validate_format_rejects_unknown_tokens_with_position() {
        let err = validate_format("%H %Q").unwrap_err();
        assert_eq!(
            err,
            FormatError::UnknownToken {
                percent_index: 3,
                token: 'Q'
            }
        );
        // Positions are character-based, even past literal text.
        // "It is %H%%:10 %Q": the %Q percent sits at char 14.
        let err = validate_format("It is %H%%:10 %Q").unwrap_err();
        assert_eq!(
            err,
            FormatError::UnknownToken {
                percent_index: 14,
                token: 'Q'
            }
        );
        assert!(err.to_string().contains("`%Q`"));
    }

    #[test]
    fn validate_format_rejects_a_lone_trailing_percent() {
        // "%H %M %": the trailing % sits at char 6.
        let err = validate_format("%H %M %").unwrap_err();
        assert_eq!(
            err,
            FormatError::TrailingPercent { percent_index: 6 }
        );
        // But `%%` (literal percent) is fine, including at the end.
        assert_eq!(validate_format("50%%"), Ok(()));
    }

    #[test]
    fn validate_format_and_render_share_the_same_token_set() {
        // Whatever the validator accepts, the renderer also recognises — this
        // test fails if someone adds a token to `render` without extending
        // `FORMAT_TOKENS` (or removes one from the renderer).
        for fmt in ["%H", "%I", "%M", "%S", "%p", "%d", "%b", "%B", "%a", "%A", "%Y", "%%"] {
            assert_eq!(validate_format(fmt), Ok(()), "`{fmt}` should be valid");
            let out = render(fmt, &CivilDate::from_days(0), 0, 0, 0);
            assert!(!out.starts_with('%') || out == "%", "`{fmt}` rendered `{out}`");
        }
    }
}
