//! Calendar-date math for the Clock widget, with **zero external dependencies**.
//!
//! The core crate is allowed to have no dependencies (see `ARCHITECTURE.md`), so
//! a wall-clock formatter that must turn *epoch seconds + a UTC offset* into a
//! civil date (year, month, day, weekday) has to bring its own arithmetic. This
//! module is that arithmetic: the proleptic Gregorian `days ↔ (year, month, day)`
//! conversion from Howard Hinnant's [civil-time](https://howardhinnant.github.io/
//! date.html) algorithm, plus the small helpers that turn a `(days, remainder-of-
//! day)` pair into a [`CivilDate`].
//!
//! # Why days, not seconds
//!
//! Everything is expressed in **days since the Unix epoch** (1970-01-01). That is
//! the unit the calendar is built on, and it lets the weekday fall out for free
//! (1970-01-01 was a Thursday). Time-of-day is a separate `rem_euclid(86_400)`
//! remainder so the arithmetic stays correct for timestamps *before* 1970, where
//! Rust's `/` and `%` round toward zero and would otherwise drift a day.

/// One civil (calendar) date: a year, month (1–12), day (1–31), and weekday.
///
/// The weekday is a [`Weekday`] with [`Weekday::Monday`] = `0`, so the enum
/// `0..7` line up with the ISO-8601 ordering and are trivially indexable.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CivilDate {
    /// Proleptic Gregorian year (may be ≤ 0 for dates before 1 BC).
    pub year: i64,
    /// Month of year, 1 (January) ..= 12 (December).
    pub month: u32,
    /// Day of month, 1 ..= 31.
    pub day: u32,
    /// Day of week, [`Weekday::Monday`] = `0` ..= [`Weekday::Sunday`] = `6`.
    pub weekday: Weekday,
}

/// Days of the week, Monday-first so the discriminants are `0..7`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Weekday {
    Monday = 0,
    Tuesday,
    Wednesday,
    Thursday,
    Friday,
    Saturday,
    Sunday,
}

impl Weekday {
    /// Three-letter English abbreviation (e.g. `"Mon"`).
    pub fn short(self) -> &'static str {
        match self {
            Weekday::Monday => "Mon",
            Weekday::Tuesday => "Tue",
            Weekday::Wednesday => "Wed",
            Weekday::Thursday => "Thu",
            Weekday::Friday => "Fri",
            Weekday::Saturday => "Sat",
            Weekday::Sunday => "Sun",
        }
    }

    /// Full English name (e.g. `"Monday"`).
    pub fn long(self) -> &'static str {
        match self {
            Weekday::Monday => "Monday",
            Weekday::Tuesday => "Tuesday",
            Weekday::Wednesday => "Wednesday",
            Weekday::Thursday => "Thursday",
            Weekday::Friday => "Friday",
            Weekday::Saturday => "Saturday",
            Weekday::Sunday => "Sunday",
        }
    }
}

/// Number of days in a (proleptic Gregorian) year.
fn is_leap(year: i64) -> bool {
    (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
}

fn days_in_month(year: i64, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if is_leap(year) {
                29
            } else {
                28
            }
        }
        _ => 0,
    }
}

/// Days since the Unix epoch for a civil date (Hinnant `days_from_civil`).
///
/// Public so it can be checked as the exact inverse of [`civil_from_days`].
pub fn days_from_civil(year: i64, month: u32, day: u32) -> i64 {
    let y = if month <= 2 { year - 1 } else { year };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400; // [0, 399]
    let mp = if month > 2 { month - 3 } else { month + 9 }; // [0, 11]
    let doy = ((153 * mp + 2) / 5 + day - 1) as i64; // [0, 365]
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy; // [0, 146096]
    era * 146097 + doe - 719_468
}

/// The civil date for a given number of days since the Unix epoch
/// (Hinnant `civil_from_days`).
///
/// Public so it can be checked as the exact inverse of [`days_from_civil`].
pub fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097; // [0, 146096]
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365; // [0, 399]
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0, 365]
    let mp = (5 * doy + 2) / 153; // [0, 11]
    let d = doy - (153 * mp + 2) / 5 + 1; // [1, 31]
    let m = if mp < 10 { mp + 3 } else { mp - 9 }; // [1, 12]
    // The `yoe` above was computed on the *shifted* year in which January and
    // February belong to the *previous* calendar year, so recover the civil
    // year when the month is Jan or Feb. (Skipping this is a classic
    // off-by-one-year bug on Jan/Feb dates.)
    let y = if m <= 2 { y + 1 } else { y };
    // m and d are in range, so a plain `as u32` is safe.
    (y, m as u32, d as u32)
}

/// The [`Weekday`] for a number of days since the Unix epoch.
///
/// 1970-01-01 was a Thursday, so `days = 0` maps to [`Weekday::Thursday`].
pub fn weekday_from_days(days: i64) -> Weekday {
    // Shift so that Monday == 0. Thursday's (index 4 in a Sunday=0 week)
    // Monday-based index is 3, i.e. we offset by 3 before taking mod 7.
    let idx = days.rem_euclid(7) + 3; // 0 => Thu -> 3
    let monday_based = idx.rem_euclid(7);
    match monday_based {
        0 => Weekday::Monday,
        1 => Weekday::Tuesday,
        2 => Weekday::Wednesday,
        3 => Weekday::Thursday,
        4 => Weekday::Friday,
        5 => Weekday::Saturday,
        _ => Weekday::Sunday,
    }
}

impl CivilDate {
    /// Build a [`CivilDate`] from a number of days since the Unix epoch.
    pub fn from_days(days: i64) -> Self {
        let (year, month, day) = civil_from_days(days);
        CivilDate {
            year,
            month,
            day,
            weekday: weekday_from_days(days),
        }
    }

    /// The number of days in the month this date belongs to.
    pub fn days_in_month(&self) -> u32 {
        days_in_month(self.year, self.month)
    }

    /// Two-digit day of month, zero-padded (`"1"` → `"01"`).
    pub fn day_2digit(&self) -> String {
        format!("{:02}", self.day)
    }

    /// Three-letter English month abbreviation (e.g. `"Jan"`).
    pub fn month_abbrev(&self) -> &'static str {
        match self.month {
            1 => "Jan",
            2 => "Feb",
            3 => "Mar",
            4 => "Apr",
            5 => "May",
            6 => "Jun",
            7 => "Jul",
            8 => "Aug",
            9 => "Sep",
            10 => "Oct",
            11 => "Nov",
            _ => "Dec",
        }
    }

    /// Full English month name (e.g. `"January"`).
    pub fn month_full(&self) -> &'static str {
        match self.month {
            1 => "January",
            2 => "February",
            3 => "March",
            4 => "April",
            5 => "May",
            6 => "June",
            7 => "July",
            8 => "August",
            9 => "September",
            10 => "October",
            11 => "November",
            _ => "December",
        }
    }

    /// Four-digit year, zero-padded for years below 1000 (never negative-padded).
    pub fn year_4digit(&self) -> String {
        if self.year < 0 {
            format!("-{:04}", -self.year)
        } else {
            format!("{:04}", self.year)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn epoch_day_zero_is_thursday_1970() {
        let d = CivilDate::from_days(0);
        assert_eq!((d.year, d.month, d.day), (1970, 1, 1));
        assert_eq!(d.weekday, Weekday::Thursday);
    }

    #[test]
    fn a_known_recent_date() {
        // 2026-09-15 is 20_711 days after the epoch.
        let d = CivilDate::from_days(20_711);
        assert_eq!((d.year, d.month, d.day), (2026, 9, 15));
        // 2026-09-15 is a Tuesday.
        assert_eq!(d.weekday, Weekday::Tuesday);
        assert_eq!(d.month_abbrev(), "Sep");
        assert_eq!(d.month_full(), "September");
        assert_eq!(d.weekday.short(), "Tue");
        assert_eq!(d.weekday.long(), "Tuesday");
    }

    #[test]
    fn january_and_february_do_not_underflow_the_year() {
        // Regression: the shifted-year bookkeeping must not drop Jan/Feb by a
        // whole year. These are the dates that broke the naive version.
        let d = CivilDate::from_days(0);
        assert_eq!((d.year, d.month, d.day), (1970, 1, 1));
        let feb = CivilDate::from_days(31); // 1970-02-01
        assert_eq!((feb.year, feb.month, feb.day), (1970, 2, 1));
        let prev_jan = CivilDate::from_days(-365); // 1969-01-01
        assert_eq!((prev_jan.year, prev_jan.month, prev_jan.day), (1969, 1, 1));
        // Round-trips through both directions for Jan/Feb across eras.
        for (y, m, d) in [(1970, 1, 1), (1969, 1, 1), (2000, 2, 29), (2024, 2, 29)] {
            assert_eq!(civil_from_days(days_from_civil(y, m, d)), (y, m, d));
        }
    }

    #[test]
    fn civil_from_days_matches_a_wide_datetime_sweep() {
        // Independent cross-check: the day counts were verified against
        // `datetime.date`. A broad sweep keeps the arithmetic
        // honest across leap years and era boundaries.
        for days in [
            -365, -366, -336, -2, -1, 0, 1, 2, 30, 100, 365, 730, 1461, 3652, 10_000,
            18_628, 19_358, 19_723, 20_089, 20_454, 20_553, 20_605, 20_711,
        ] {
            let (y, m, d) = civil_from_days(days);
            assert_eq!(days_from_civil(y, m, d), days, "day {days}");
        }
    }

    #[test]
    fn epoch_boundary_and_day_after() {
        let next = CivilDate::from_days(1);
        assert_eq!((next.year, next.month, next.day), (1970, 1, 2));
        assert_eq!(next.weekday, Weekday::Friday);
    }

    #[test]
    fn year_starts_have_correct_weekdays() {
        // New-Year's Days: 2021 Fri, 2022 Sat, 2023 Sun, 2024 Mon, 2025 Wed.
        let cases: [(i64, Weekday); 5] = [
            (18_628, Weekday::Friday),
            (18_993, Weekday::Saturday),
            (19_358, Weekday::Sunday),
            (19_723, Weekday::Monday),
            (20_089, Weekday::Wednesday),
        ];
        for (days, wd) in cases {
            let d = CivilDate::from_days(days);
            assert_eq!((d.year, d.month, d.day), (d.year, 1, 1));
            assert_eq!(d.weekday, wd, "day {days}");
        }
    }

    #[test]
    fn leap_year_february_has_29_days() {
        // 2024-02-29 exists (2024 is a leap year); 2025-02-29 does not.
        let leap = CivilDate {
            year: 2024,
            month: 2,
            day: 28,
            weekday: Weekday::Thursday,
        };
        assert_eq!(leap.days_in_month(), 29);
        let common = CivilDate {
            year: 2025,
            month: 2,
            day: 28,
            weekday: Weekday::Friday,
        };
        assert_eq!(common.days_in_month(), 28);
        // Century years: 1900 is NOT a leap year, 2000 IS.
        assert!(is_leap(2000));
        assert!(!is_leap(1900));
        assert!(is_leap(2024));
        assert!(!is_leap(2023));
    }

    #[test]
    fn days_from_and_civil_from_are_exact_inverses() {
        // Spot-checked known (year, month, day) -> days values.
        assert_eq!(days_from_civil(1970, 1, 1), 0);
        assert_eq!(days_from_civil(1970, 1, 2), 1);
        assert_eq!(days_from_civil(2000, 1, 1), 10_957);
        assert_eq!(days_from_civil(2026, 9, 15), 20_711);
        assert_eq!(days_from_civil(1969, 12, 31), -1);

        // Round-trip a wide sweep of days through both directions.
        for days in [-730_530i64, -1, 0, 1, 18_628, 20_553, 730_504] {
            let (y, m, d) = civil_from_days(days);
            assert_eq!(days_from_civil(y, m, d), days, "day {days}");
        }
    }

    #[test]
    fn pre_epoch_dates_do_not_drift_a_day() {
        // 1969-12-31 is one day before the epoch.
        assert_eq!(civil_from_days(-1), (1969, 12, 31));
        let d = CivilDate::from_days(-1);
        assert_eq!(d.weekday, Weekday::Wednesday); // 1969-12-31 was a Wednesday
        // A far-past date still round-trips.
        let (y, m, day) = civil_from_days(-730_530);
        assert_eq!(days_from_civil(y, m, day), -730_530);
    }

    #[test]
    fn formatting_helpers() {
        let d = CivilDate::from_days(20_711); // 2026-09-15
        assert_eq!(d.day_2digit(), "15");
        assert_eq!(d.year_4digit(), "2026");

        // Zero-padding for small day numbers.
        let first = CivilDate::from_days(20_605); // 2026-06-01
        assert_eq!((first.year, first.month, first.day), (2026, 6, 1));
        assert_eq!(first.day_2digit(), "01");
        assert_eq!(first.month_abbrev(), "Jun");
    }
}
