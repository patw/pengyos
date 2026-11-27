//! The built-in VNC view: a canvas that draws the VM's framebuffer (scaled to
//! fit, or 1:1) and turns the mouse and the keyboard into VNC input. Keys go by
//! physical position (keymap.rs), so the guest's layout decides what they type.
//! It takes the keyboard after a click on the screen and gives it back on a
//! click anywhere else.

use cosmic::iced::widget::canvas::{self, Event};
use cosmic::iced::{keyboard, mouse, Color, Point, Rectangle, Size};
use cosmic::iced::widget::image::Handle;

use crate::backend::Input;

pub struct View<'a> {
    pub image: Option<&'a Handle>,
    pub width: u16,
    pub height: u16,
    pub fit: bool,
    pub on_input: fn(Input) -> crate::app::Message,
    pub on_focus: fn(bool) -> crate::app::Message,
}

#[derive(Default)]
pub struct State {
    focused: bool,
    buttons: u8,
}

/// Where the framebuffer is drawn inside `size`: letterboxed to fit, or 1:1
/// centred (the edges cut off when the window is smaller).
pub fn placement(size: Size, w: u16, h: u16, fit: bool) -> Rectangle {
    let (fw, fh) = (w.max(1) as f32, h.max(1) as f32);
    let scale = if fit { (size.width / fw).min(size.height / fh) } else { 1.0 };
    let (dw, dh) = (fw * scale, fh * scale);
    Rectangle::new(Point::new((size.width - dw) / 2.0, (size.height - dh) / 2.0), Size::new(dw, dh))
}

/// A point in the canvas to a framebuffer pixel (None outside the picture).
pub fn to_framebuffer(p: Point, place: Rectangle, w: u16, h: u16) -> Option<(u16, u16)> {
    if !place.contains(p) {
        return None;
    }
    let x = ((p.x - place.x) / place.width * w as f32).floor().clamp(0.0, w.saturating_sub(1) as f32);
    let y = ((p.y - place.y) / place.height * h as f32).floor().clamp(0.0, h.saturating_sub(1) as f32);
    Some((x as u16, y as u16))
}

fn bit(b: mouse::Button) -> u8 {
    match b {
        mouse::Button::Left => 1,
        mouse::Button::Middle => 2,
        mouse::Button::Right => 4,
        _ => 0,
    }
}

fn code_name(k: &keyboard::key::Physical) -> Option<String> {
    match k {
        keyboard::key::Physical::Code(c) => Some(format!("{c:?}")),
        _ => None,
    }
}

impl canvas::Program<crate::app::Message, cosmic::Theme, cosmic::Renderer> for View<'_> {
    type State = State;

    fn update(
        &self,
        state: &mut State,
        event: &Event,
        bounds: Rectangle,
        cursor: mouse::Cursor,
    ) -> Option<canvas::Action<crate::app::Message>> {
        let place = placement(bounds.size(), self.width, self.height, self.fit);
        let at = cursor.position_in(bounds).and_then(|p| to_framebuffer(p, place, self.width, self.height));
        let pointer = |buttons: u8, (x, y): (u16, u16)| (self.on_input)(Input::Pointer { buttons, x, y });
        match event {
            Event::Mouse(mouse::Event::ButtonPressed(b)) => {
                let Some(p) = at else {
                    if state.focused && cursor.position_in(bounds).is_none() {
                        state.focused = false;
                        return Some(canvas::Action::publish((self.on_focus)(false)));
                    }
                    return None;
                };
                if !state.focused {
                    // The first click only gives the screen the keyboard and
                    // pointer; it does not land in the guest.
                    state.focused = true;
                    return Some(canvas::Action::publish((self.on_focus)(true)).and_capture());
                }
                state.buttons |= bit(*b);
                Some(canvas::Action::publish(pointer(state.buttons, p)).and_capture())
            }
            Event::Mouse(mouse::Event::ButtonReleased(b)) if state.buttons & bit(*b) != 0 => {
                state.buttons &= !bit(*b);
                let p = at.unwrap_or((0, 0));
                Some(canvas::Action::publish(pointer(state.buttons, p)).and_capture())
            }
            Event::Mouse(mouse::Event::CursorMoved { .. }) => at.map(|p| canvas::Action::publish(pointer(state.buttons, p))),
            Event::Mouse(mouse::Event::WheelScrolled { delta }) => {
                let p = at?;
                let y = match delta {
                    mouse::ScrollDelta::Lines { y, .. } => *y,
                    mouse::ScrollDelta::Pixels { y, .. } => *y,
                };
                if y == 0.0 {
                    return None;
                }
                let wheel = if y > 0.0 { 8 } else { 16 };
                // Press and release: one notch. The release follows in the next event.
                Some(canvas::Action::publish(pointer(state.buttons | wheel, p)).and_capture())
            }
            Event::Keyboard(keyboard::Event::KeyPressed { physical_key, .. }) if state.focused => {
                let (scancode, keysym) = crate::keymap::lookup(&code_name(physical_key)?)?;
                Some(canvas::Action::publish((self.on_input)(Input::Key { down: true, keysym, scancode })).and_capture())
            }
            Event::Keyboard(keyboard::Event::KeyReleased { physical_key, .. }) if state.focused => {
                let (scancode, keysym) = crate::keymap::lookup(&code_name(physical_key)?)?;
                Some(canvas::Action::publish((self.on_input)(Input::Key { down: false, keysym, scancode })).and_capture())
            }
            _ => None,
        }
    }

    fn draw(
        &self,
        _state: &State,
        renderer: &cosmic::Renderer,
        _theme: &cosmic::Theme,
        bounds: Rectangle,
        _cursor: mouse::Cursor,
    ) -> Vec<canvas::Geometry<cosmic::Renderer>> {
        let mut frame = canvas::Frame::new(renderer, bounds.size());
        frame.fill_rectangle(Point::ORIGIN, bounds.size(), Color::BLACK);
        if let Some(h) = self.image {
            let place = placement(bounds.size(), self.width, self.height, self.fit);
            frame.draw_image(place, canvas::Image::new(h.clone()));
        }
        vec![frame.into_geometry()]
    }

    fn mouse_interaction(&self, state: &State, bounds: Rectangle, cursor: mouse::Cursor) -> mouse::Interaction {
        // The guest draws its own pointer (USB tablet): hide ours over the picture
        // once the screen has the input.
        let place = placement(bounds.size(), self.width, self.height, self.fit);
        match cursor.position_in(bounds) {
            Some(p) if state.focused && place.contains(p) => mouse::Interaction::Hidden,
            Some(p) if place.contains(p) => mouse::Interaction::Pointer,
            _ => mouse::Interaction::default(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fit_letterboxes_and_maps_back() {
        let place = placement(Size::new(1000.0, 500.0), 800, 600, true);
        assert_eq!(place.height, 500.0);
        assert!((place.width - 666.666).abs() < 0.01);
        assert_eq!(to_framebuffer(Point::new(500.0, 250.0), place, 800, 600), Some((400, 300)));
        assert_eq!(to_framebuffer(Point::new(10.0, 250.0), place, 800, 600), None);
        let one = placement(Size::new(400.0, 300.0), 800, 600, false);
        assert_eq!(one.x, -200.0);
        assert_eq!(to_framebuffer(Point::new(0.0, 0.0), one, 800, 600), Some((200, 150)));
    }
}
