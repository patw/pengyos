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
//!    in `main.rs`); this table is only consulted when there is nothing else.
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
}
