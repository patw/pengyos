//! "TJ Max" resolution.
//!
//! The kernel only publishes a hard limit for *some* sensors: `hwmon`'s
//! `<base>_crit` exists for NVMe and for Intel `coretemp`, and is absent for
//! `k10temp` and for AMD iGPUs. Without a limit a monitor cannot answer the one
//! question people actually ask it — "am I too hot?" — so this module supplies a
//! documented fallback, keyed off the CPU family/model that `hwall-core` already
//! reports as device properties.
//!
//! Rules of the house:
//!
//! 1. A limit the firmware or kernel published **always wins** (see `sensor_limit`
//!    below); this table is only consulted when there is nothing else.
//! 2. Every entry cites why it is that number, because a wrong limit produces a
//!    red number nobody can explain, which is worse than no red number.
//! 3. The resolved limit is shown in the device header, so the colour is never
//!    magic.
//!
//! Values are `Tjmax`/`Tctl` limits in degrees Celsius.

#[derive(Debug, Clone, Copy)]
pub struct TjMax {
    pub celsius: f64,
    /// Short justification, e.g. "Zen3/Zen4" - shown in the UI.
    pub note: &'static str,
}

const fn tj(celsius: f64, note: &'static str) -> TjMax {
    TjMax { celsius, note }
}

/// Tjmax for a CPU, from `/proc/cpuinfo`'s `cpu family` / `model`, which
/// `hwall-core` exposes as the "CPU family" and "Model number" properties.
///
/// The family numbers are the ones in the kernel's `cpuinfo_x86` scheme, i.e. the
/// decimal `cpu family` field (`25` = 0x19), not the marketing name.
pub fn cpu(vendor: Option<&str>, family: Option<u32>, _model: Option<u32>) -> Option<TjMax> {
    match (vendor, family) {
        // ---- AMD ------------------------------------------------------------
        // 0x17 = 23: Zen, Zen+, Zen2 (Ryzen 1000..3000, 4000U/H). Tctl Tjmax 95.
        (Some("AuthenticAMD"), Some(23)) => Some(tj(95.0, "Zen/Zen+/Zen2")),
        // 0x19 = 25: Zen3, Zen3+, Zen4 (Ryzen 5000/6000/7000/8000). Tjmax 95.
        (Some("AuthenticAMD"), Some(25)) => Some(tj(95.0, "Zen3/Zen3+/Zen4")),
        // 0x1A = 26: Zen5 (Ryzen 9000/AI 300). Tjmax 95.
        (Some("AuthenticAMD"), Some(26)) => Some(tj(95.0, "Zen5")),
        // 0x0F = 15: the pre-Zen families are 90 C, but they are rare enough that
        // guessing would do more harm than good, so no entry.
        (Some("AuthenticAMD"), _) => Some(tj(95.0, "AMD unknown family")),
        // ---- Intel ----------------------------------------------------------
        // Family 6 covers everything from Pentium Pro to Arrow Lake; Tjmax has
        // been 100 C across Skylake..Arrow Lake for desktop and most mobile, with
        // a handful of 105 C mobile parts that the kernel's coretemp `_crit`
        // overrides anyway.
        (Some("GenuineIntel"), Some(6)) => Some(tj(100.0, "Intel family 6")),
        _ => None,
    }
}

/// Fallback for a device class when the sensor is a temperature and neither the
/// kernel nor the CPU table has an answer.
pub fn class_default(device_class: hwall_core::DeviceClass) -> Option<TjMax> {
    use hwall_core::DeviceClass;
    match device_class {
        // Discrete GPUs throttle in the upper 90s; AMD "junction" limits are
        // 100-110 and NVIDIA's are 90-95, so 95 flags late but never falsely.
        DeviceClass::Gpu => Some(TjMax {
            celsius: 95.0,
            note: "GPU default",
        }),
        // NVMe drives publish `_crit` (usually 84-85 C) so this rarely applies.
        DeviceClass::Storage => Some(TjMax {
            celsius: 80.0,
            note: "storage default",
        }),
        _ => None,
    }
}

/// A resolved limit: the number the red/amber colour is measured against, and
/// where it came from, so the UI can say so.
#[derive(Debug, Clone, Copy)]
pub struct Limit {
    pub celsius: f64,
    pub note: &'static str,
}

/// The limit a reading is judged against.
///
/// Order matters: a limit the firmware/kernel actually reports always wins. The
/// class defaults below only fill the gap where sysfs has no `*_crit`/`*_max`
/// at all - which is the normal case for `k10temp` and for AMD iGPUs - and they
/// are deliberately conservative, because a red number that nobody can explain
/// is worse than no red number. The resolved limit is printed in the device
/// section header so the colour is never magic.
///
/// Shared by both front-ends (the COSMIC window and the terminal view).
pub fn sensor_limit(device: &hwall_core::Device, sensor: &hwall_core::Sensor) -> Option<Limit> {
    use hwall_core::{DeviceClass, Unit};
    let kernel = |celsius: f64, note: &'static str| Limit { celsius, note };
    // A temperature limit outside a plausible range is a sentinel, not a limit:
    // NVMe reports "no threshold set" as 65261.85 °C (0xFFFF K) on its extra
    // sensors. For other units the kernel's number is taken as it is.
    let usable = |value: &f64| {
        value.is_finite() && *value > 0.0 && (sensor.unit != Unit::Celsius || plausible(*value))
    };
    if let Some(critical) = sensor.critical.filter(usable) {
        return Some(kernel(critical, "kernel crit"));
    }
    if let Some(max) = sensor.max.filter(usable) {
        return Some(kernel(max, "kernel max"));
    }
    if sensor.unit != Unit::Celsius {
        return None;
    }
    if let Some(limit) = sysfs_limit(&sensor.source) {
        return Some(limit);
    }
    if device.class == DeviceClass::Cpu {
        let vendor = device.vendor.as_deref();
        let family = property_u32(device, "cpu_family");
        let model = property_u32(device, "model_number");
        if let Some(limit) = cpu(vendor, family, model) {
            return Some(Limit {
                celsius: limit.celsius,
                note: limit.note,
            });
        }
    }
    class_default(device.class).map(|limit| Limit {
        celsius: limit.celsius,
        note: limit.note,
    })
}

/// The limit shown for a whole device. A limit the kernel published for any of
/// its temperatures beats every table entry; among limits of the same kind the
/// highest wins. So an NVMe drive shows its composite sensor's `crit` rather
/// than the storage default its unlimited extra sensors fall back to, and the
/// window, the terminal view and `--check` always name the same source.
pub fn device_limit(device: &hwall_core::Device) -> Option<Limit> {
    let rank = |limit: &Limit| (limit.note.starts_with("kernel"), limit.celsius);
    device
        .sensors
        .iter()
        .filter(|sensor| sensor.unit == hwall_core::Unit::Celsius)
        .filter_map(|sensor| sensor_limit(device, sensor))
        .fold(None, |best: Option<Limit>, limit| match best {
            Some(best) if rank(&best) >= rank(&limit) => Some(best),
            _ => Some(limit),
        })
}

/// The kernel's own limit for a temperature, read from sysfs next to its input.
///
/// hwall-core drops `min`/`max`/`crit` from every sensor whose value lm-sensors
/// supplied, because a libsensors `compute` line may rescale the value and the
/// raw limits would no longer match it. PengyOS ships lm-sensors, so on a real
/// install *every* hwmon sensor loses its kernel limit that way, and "the kernel
/// wins" would never apply. Temperatures are not rescaled by any sensors.conf we
/// ship, and the hwmon ABI fixes them at millidegrees, so for a Celsius sensor
/// whose `source` is a `tempN_input` file the sibling `tempN_crit` (or
/// `_emergency`, then `_max`) is the kernel's answer. Read once per path: limits
/// do not change while the machine runs.
fn sysfs_limit(source: &str) -> Option<Limit> {
    use std::collections::HashMap;
    use std::sync::{Mutex, OnceLock};
    static CACHE: OnceLock<Mutex<HashMap<String, Option<Limit>>>> = OnceLock::new();
    let cache = CACHE.get_or_init(Default::default);
    if let Some(known) = cache.lock().ok()?.get(source) {
        return *known;
    }
    let found = read_sysfs_limit(std::path::Path::new(source));
    if let Ok(mut map) = cache.lock() {
        map.insert(source.to_owned(), found);
    }
    found
}

fn read_sysfs_limit(input: &std::path::Path) -> Option<Limit> {
    let name = input.file_name()?.to_str()?;
    let base = name
        .strip_suffix("_input")
        .filter(|b| b.starts_with("temp"))?;
    let dir = input.parent()?;
    let read = |suffix: &str| -> Option<f64> {
        let text = std::fs::read_to_string(dir.join(format!("{base}_{suffix}"))).ok()?;
        let millis: f64 = text.trim().parse().ok()?;
        let celsius = millis / 1000.0;
        plausible(celsius).then_some(celsius)
    };
    if let Some(celsius) = read("crit").or_else(|| read("emergency")) {
        return Some(Limit {
            celsius,
            note: "kernel crit",
        });
    }
    read("max").map(|celsius| Limit {
        celsius,
        note: "kernel max",
    })
}

/// A temperature limit worth believing: above room temperature, below anything
/// silicon survives. Outside it the number is a "not set" sentinel.
fn plausible(celsius: f64) -> bool {
    celsius.is_finite() && (20.0..=150.0).contains(&celsius)
}

fn property_u32(device: &hwall_core::Device, name: &str) -> Option<u32> {
    match device.properties.get(name)? {
        hwall_core::PropertyValue::Unsigned(value) => u32::try_from(*value).ok(),
        hwall_core::PropertyValue::Integer(value) => u32::try_from(*value).ok(),
        hwall_core::PropertyValue::String(value) => value.trim().parse().ok(),
        _ => None,
    }
}

/// How close a reading is to its limit.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Heat {
    /// within 15 °C of the limit
    Warm,
    /// within 5 °C of the limit
    Hot,
}

/// Red inside 5 °C of the limit, amber inside 15 °C, nothing otherwise.
pub fn heat(value: Option<f64>, limit: Option<f64>) -> Option<Heat> {
    let (value, limit) = (value?, limit?);
    if value >= limit - 5.0 {
        Some(Heat::Hot)
    } else if value >= limit - 15.0 {
        Some(Heat::Warm)
    } else {
        None
    }
}

/// hwall-app's status strings that mean "nothing to say".
pub fn is_ok_status(status: &str) -> bool {
    matches!(status, "Normal" | "ok" | "OK" | "")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zen_families_are_95() {
        for family in [23, 25, 26] {
            let limit = cpu(Some("AuthenticAMD"), Some(family), Some(0)).expect("known family");
            assert_eq!(limit.celsius, 95.0, "family {family}");
        }
    }

    #[test]
    fn intel_family_six_is_100() {
        let limit = cpu(Some("GenuineIntel"), Some(6), Some(0x97)).expect("known family");
        assert_eq!(limit.celsius, 100.0);
    }

    #[test]
    fn unknown_vendor_has_no_answer() {
        assert!(cpu(Some("CyrixInstead"), Some(6), None).is_none());
        assert!(cpu(None, None, None).is_none());
    }

    #[test]
    fn unknown_amd_family_still_answers() {
        // Better a conservative Zen number than nothing: it is shown in the header.
        let limit = cpu(Some("AuthenticAMD"), Some(15), None).expect("amd");
        assert_eq!(limit.celsius, 95.0);
        assert!(limit.note.contains("unknown family"));
    }

    #[test]
    fn class_defaults_only_cover_gpu_and_storage() {
        use hwall_core::DeviceClass;
        assert_eq!(
            class_default(DeviceClass::Gpu).map(|l| l.celsius),
            Some(95.0)
        );
        assert_eq!(
            class_default(DeviceClass::Storage).map(|l| l.celsius),
            Some(80.0)
        );
        assert!(class_default(DeviceClass::Cpu).is_none());
        assert!(class_default(DeviceClass::Memory).is_none());
    }

    #[test]
    fn heat_bands() {
        assert_eq!(heat(Some(70.0), Some(95.0)), None);
        assert_eq!(heat(Some(80.0), Some(95.0)), Some(Heat::Warm));
        assert_eq!(heat(Some(90.0), Some(95.0)), Some(Heat::Hot));
        assert_eq!(heat(None, Some(95.0)), None);
        assert_eq!(heat(Some(99.0), None), None);
    }

    fn temp(id: &str, critical: Option<f64>) -> hwall_core::Sensor {
        let mut sensor = hwall_core::Sensor::new(
            id,
            id,
            hwall_core::SensorKind::Temperature,
            hwall_core::Unit::Celsius,
            Some(40.0),
            "test",
            hwall_core::Identification::KernelLabel,
        );
        sensor.critical = critical;
        sensor
    }

    fn intel(sensors: Vec<hwall_core::Sensor>) -> hwall_core::Device {
        let mut device = hwall_core::Device::new("cpu0", hwall_core::DeviceClass::Cpu, "Intel CPU");
        device.vendor = Some("GenuineIntel".to_owned());
        device.properties.insert(
            "cpu_family".to_owned(),
            hwall_core::PropertyValue::Unsigned(6),
        );
        device.sensors = sensors;
        device
    }

    #[test]
    fn device_limit_names_the_kernel_on_a_tie_in_any_order() {
        // coretemp: Package has no _crit (table says 100), the cores say 100.
        let first = intel(vec![temp("package", None), temp("core0", Some(100.0))]);
        let last = intel(vec![temp("core0", Some(100.0)), temp("package", None)]);
        for device in [first, last] {
            let limit = device_limit(&device).expect("limit");
            assert_eq!(limit.celsius, 100.0);
            assert_eq!(limit.note, "kernel crit");
        }
    }

    #[test]
    fn device_limit_takes_the_highest() {
        let device = intel(vec![temp("a", Some(90.0)), temp("b", Some(105.0))]);
        assert_eq!(device_limit(&device).map(|l| l.celsius), Some(105.0));
    }

    #[test]
    fn sysfs_limit_reads_crit_then_max_next_to_the_input() {
        let dir = std::env::temp_dir().join(format!("pengyos-hwmon-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("temp1_input"), "41000\n").unwrap();
        std::fs::write(dir.join("temp1_crit"), "105000\n").unwrap();
        std::fs::write(dir.join("temp1_max"), "90000\n").unwrap();
        std::fs::write(dir.join("temp2_input"), "41000\n").unwrap();
        std::fs::write(dir.join("temp2_max"), "84850\n").unwrap();
        std::fs::write(dir.join("temp3_input"), "41000\n").unwrap();
        std::fs::write(dir.join("temp3_crit"), "0\n").unwrap();

        let one = read_sysfs_limit(&dir.join("temp1_input")).expect("crit");
        assert_eq!((one.celsius, one.note), (105.0, "kernel crit"));
        let two = read_sysfs_limit(&dir.join("temp2_input")).expect("max");
        assert_eq!((two.celsius, two.note), (84.85, "kernel max"));
        assert!(
            read_sysfs_limit(&dir.join("temp3_input")).is_none(),
            "0 is no limit"
        );
        assert!(
            read_sysfs_limit(&dir.join("fan1_input")).is_none(),
            "temperatures only"
        );
        assert!(read_sysfs_limit(std::path::Path::new("lm-sensors")).is_none());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_sensor_without_limits_falls_back_to_sysfs_before_the_table() {
        let dir = std::env::temp_dir().join(format!("pengyos-hwmon-test2-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("temp1_crit"), "105000\n").unwrap();
        let mut sensor = temp("core0", None);
        sensor.source = dir.join("temp1_input").to_string_lossy().into_owned();
        let device = intel(vec![sensor]);
        let limit = sensor_limit(&device, &device.sensors[0]).expect("limit");
        assert_eq!((limit.celsius, limit.note), (105.0, "kernel crit"));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn nvme_no_threshold_sentinel_is_not_a_limit() {
        let mut composite = temp("temp1", Some(75.85));
        composite.max = Some(72.85);
        let mut sensor2 = temp("temp2", None);
        sensor2.max = Some(65261.85);
        let mut drive = hwall_core::Device::new("nvme0", hwall_core::DeviceClass::Storage, "SSD");
        drive.sensors = vec![composite, sensor2];
        let second = sensor_limit(&drive, &drive.sensors[1]).expect("falls back");
        assert_eq!((second.celsius, second.note), (80.0, "storage default"));
        let limit = device_limit(&drive).expect("limit");
        assert_eq!((limit.celsius, limit.note), (75.85, "kernel crit"));
    }
}
