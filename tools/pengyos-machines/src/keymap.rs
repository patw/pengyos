//! Physical keys to what QEMU's extended key event wants: the PC scancode
//! ("qnum": an 0xE0-prefixed code has 0x80 set) and an X keysym for servers
//! without the extension. Keys are named by their W3C `code` (KeyA, Digit1,
//! ShiftLeft...), which is how iced names physical keys, so the position on the
//! keyboard is sent, not the letter: the guest's own layout decides the letter.

/// (scancode, keysym) for a W3C key code name, or None for a key we do not send.
pub fn lookup(code: &str) -> Option<(u32, u32)> {
    let letters = "QWERTYUIOPASDFGHJKLZXCVBNM";
    let letter_codes = [
        0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1e, 0x1f, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25,
        0x26, 0x2c, 0x2d, 0x2e, 0x2f, 0x30, 0x31, 0x32,
    ];
    if let Some(l) = code.strip_prefix("Key") {
        let c = l.chars().next()?;
        let i = letters.find(c)?;
        if l.len() == 1 {
            return Some((letter_codes[i], c.to_ascii_lowercase() as u32));
        }
    }
    if let Some(d) = code.strip_prefix("Digit") {
        let n: u32 = d.parse().ok()?;
        if n <= 9 {
            let sc = if n == 0 { 0x0b } else { 0x01 + n };
            return Some((sc, 0x30 + n));
        }
    }
    if let Some(f) = code.strip_prefix('F') {
        if let Ok(n) = f.parse::<u32>() {
            let sc = match n {
                1..=10 => 0x3a + n,
                11 => 0x57,
                12 => 0x58,
                _ => return None,
            };
            return Some((sc, 0xffbd + n));
        }
    }
    let (sc, sym) = match code {
        "Escape" => (0x01, 0xff1b),
        "Minus" => (0x0c, b'-' as u32),
        "Equal" => (0x0d, b'=' as u32),
        "Backspace" => (0x0e, 0xff08),
        "Tab" => (0x0f, 0xff09),
        "BracketLeft" => (0x1a, b'[' as u32),
        "BracketRight" => (0x1b, b']' as u32),
        "Enter" => (0x1c, 0xff0d),
        "ControlLeft" => (0x1d, 0xffe3),
        "Semicolon" => (0x27, b';' as u32),
        "Quote" => (0x28, b'\'' as u32),
        "Backquote" => (0x29, b'`' as u32),
        "ShiftLeft" => (0x2a, 0xffe1),
        "Backslash" => (0x2b, b'\\' as u32),
        "Comma" => (0x33, b',' as u32),
        "Period" => (0x34, b'.' as u32),
        "Slash" => (0x35, b'/' as u32),
        "ShiftRight" => (0x36, 0xffe2),
        "NumpadMultiply" => (0x37, 0xffaa),
        "AltLeft" => (0x38, 0xffe9),
        "Space" => (0x39, 0x20),
        "CapsLock" => (0x3a, 0xffe5),
        "NumLock" => (0x45, 0xff7f),
        "ScrollLock" => (0x46, 0xff14),
        "Numpad7" => (0x47, 0xffb7),
        "Numpad8" => (0x48, 0xffb8),
        "Numpad9" => (0x49, 0xffb9),
        "NumpadSubtract" => (0x4a, 0xffad),
        "Numpad4" => (0x4b, 0xffb4),
        "Numpad5" => (0x4c, 0xffb5),
        "Numpad6" => (0x4d, 0xffb6),
        "NumpadAdd" => (0x4e, 0xffab),
        "Numpad1" => (0x4f, 0xffb1),
        "Numpad2" => (0x50, 0xffb2),
        "Numpad3" => (0x51, 0xffb3),
        "Numpad0" => (0x52, 0xffb0),
        "NumpadDecimal" => (0x53, 0xffae),
        "IntlBackslash" => (0x56, b'<' as u32),
        "NumpadEnter" => (0x9c, 0xff8d),
        "ControlRight" => (0x9d, 0xffe4),
        "NumpadDivide" => (0xb5, 0xffaf),
        "PrintScreen" => (0xb7, 0xff61),
        "AltRight" => (0xb8, 0xffea),
        "Home" => (0xc7, 0xff50),
        "ArrowUp" => (0xc8, 0xff52),
        "PageUp" => (0xc9, 0xff55),
        "ArrowLeft" => (0xcb, 0xff51),
        "ArrowRight" => (0xcd, 0xff53),
        "End" => (0xcf, 0xff57),
        "ArrowDown" => (0xd0, 0xff54),
        "PageDown" => (0xd1, 0xff56),
        "Insert" => (0xd2, 0xff63),
        "Delete" => (0xd3, 0xffff),
        "SuperLeft" | "MetaLeft" => (0xdb, 0xffeb),
        "SuperRight" | "MetaRight" => (0xdc, 0xffec),
        "ContextMenu" => (0xdd, 0xff67),
        _ => return None,
    };
    Some((sc, sym))
}

/// Ctrl+Alt+Del as a sequence of (down, keysym, scancode), for the button: the
/// desktop keeps that chord for itself.
pub fn ctrl_alt_del() -> Vec<(bool, u32, u32)> {
    let (c, a, d) = (lookup("ControlLeft").unwrap(), lookup("AltLeft").unwrap(), lookup("Delete").unwrap());
    vec![(true, c.1, c.0), (true, a.1, a.0), (true, d.1, d.0), (false, d.1, d.0), (false, a.1, a.0), (false, c.1, c.0)]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn letters_digits_and_function_keys() {
        assert_eq!(lookup("KeyA"), Some((0x1e, b'a' as u32)));
        assert_eq!(lookup("KeyM"), Some((0x32, b'm' as u32)));
        assert_eq!(lookup("KeyQ"), Some((0x10, b'q' as u32)));
        assert_eq!(lookup("Digit1"), Some((0x02, b'1' as u32)));
        assert_eq!(lookup("Digit0"), Some((0x0b, b'0' as u32)));
        assert_eq!(lookup("F1"), Some((0x3b, 0xffbe)));
        assert_eq!(lookup("F10"), Some((0x44, 0xffc7)));
        assert_eq!(lookup("F12"), Some((0x58, 0xffc9)));
        assert_eq!(lookup("Keyboard"), None);
        assert_eq!(lookup("F13"), None);
    }

    #[test]
    fn extended_keys_have_the_e0_bit() {
        assert_eq!(lookup("ArrowUp").unwrap().0, 0xc8);
        assert_eq!(lookup("Delete").unwrap().0, 0xd3);
        assert_eq!(lookup("SuperLeft"), lookup("MetaLeft"));
        assert_eq!(ctrl_alt_del().len(), 6);
    }

    #[test]
    fn no_two_keys_share_a_scancode() {
        let mut names: Vec<String> = "QWERTYUIOPASDFGHJKLZXCVBNM".chars().map(|c| format!("Key{c}")).collect();
        names.extend((0..10).map(|d| format!("Digit{d}")));
        names.extend((1..13).map(|f| format!("F{f}")));
        names.extend(
            "Escape Minus Equal Backspace Tab Enter Space ShiftLeft ShiftRight ControlLeft ControlRight AltLeft AltRight ArrowUp ArrowDown ArrowLeft ArrowRight Home End PageUp PageDown Insert Delete SuperLeft"
                .split(' ')
                .map(String::from),
        );
        let mut codes: Vec<u32> = names.iter().map(|n| lookup(n).unwrap().0).collect();
        let n = codes.len();
        codes.sort();
        codes.dedup();
        assert_eq!(codes.len(), n);
    }
}
