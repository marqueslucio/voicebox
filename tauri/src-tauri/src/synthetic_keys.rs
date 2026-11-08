//! Synthetic keyboard event posting for the auto-paste pipeline.
//!
//! `send_paste` fires the four-event paste sequence onto the OS input
//! pipeline so the focused app performs its native paste action against
//! whatever the clipboard module has just staged.
//!
//! - **macOS** — Cmd down (`flagsChanged`), V down, V up (both with the
//!   Cmd flag), Cmd up (`flagsChanged`, flags cleared) via `CGEventPost`
//!   at `kCGHIDEventTap`. The modifier transitions are typed
//!   `flagsChanged` to match hardware — Electron/Chromium only feeds
//!   `flagsChanged` into its tracked modifier state and drops the paste
//!   otherwise (see the note in [`paste_events`]).
//!   Accessibility permission is load-bearing: without it the system
//!   swallows the events silently, so callers must gate on
//!   [`crate::accessibility::is_trusted`].
//! - **Windows** — Ctrl down, V down, V up, Ctrl up via `SendInput`. No
//!   permission gate, but UAC/UIPI blocks delivery into elevated target
//!   windows when we run non-elevated — nothing we can do short of also
//!   running elevated.
//!
//! On macOS the V keycode is resolved per-layout by
//! [`crate::keyboard_layout`] — Cmd+V is matched against the layout-
//! translated character via NSMenu key equivalents, so hardcoding
//! `kVK_ANSI_V` (the QWERTY V position) would fire Cmd+. on Dvorak. The
//! resolved keycode is read once per paste from an atomic; the cache is
//! primed at startup and refreshed on layout change.
//!
//! Windows hardcodes `VK_V`. `SendInput` with `wVk = VK_V` makes the
//! target receive `WM_KEYDOWN` with `wParam = VK_V` regardless of the
//! active layout, and most Windows apps treat that as Ctrl+V (the same
//! reason `Send "^v"` works in AutoHotkey on Dvorak Windows).

#[cfg(target_os = "macos")]
use std::ffi::c_void;

#[cfg(target_os = "macos")]
mod ffi {
    use std::ffi::c_void;

    #[repr(C)]
    pub struct CGEvent {
        _opaque: [u8; 0],
    }
    pub type CGEventRef = *mut CGEvent;

    #[repr(C)]
    pub struct CGEventSource {
        _opaque: [u8; 0],
    }
    pub type CGEventSourceRef = *mut CGEventSource;

    pub type CGEventTapLocation = u32;
    pub type CGKeyCode = u16;
    pub type CGEventFlags = u64;
    pub type CGEventSourceStateID = i32;

    /// `kCGHIDEventTap` — posted events enter at the HID level so every
    /// downstream tap (including the target app) sees them exactly as if the
    /// hardware had produced them.
    pub const K_CG_HID_EVENT_TAP: CGEventTapLocation = 0;

    /// `kCGEventSourceStateHIDSystemState` — mimics hardware, which is what
    /// we want: modifier bookkeeping inside target apps stays consistent.
    pub const K_CG_EVENT_SOURCE_STATE_HID_SYSTEM_STATE: CGEventSourceStateID = 1;

    /// `kCGEventFlagMaskCommand` — the Cmd modifier bit inside `CGEventFlags`.
    pub const K_CG_EVENT_FLAG_MASK_COMMAND: CGEventFlags = 0x00100000;

    /// `kVK_Command` (left Cmd).
    pub const KEYCODE_LEFT_CMD: CGKeyCode = 0x37;

    pub type CGEventType = u32;

    /// `kCGEventFlagsChanged` — the event type hardware produces for pure
    /// modifier transitions (Cmd/Shift/Option/Ctrl press and release).
    pub const K_CG_EVENT_FLAGS_CHANGED: CGEventType = 12;

    #[link(name = "CoreGraphics", kind = "framework")]
    extern "C" {
        pub fn CGEventSourceCreate(state_id: CGEventSourceStateID) -> CGEventSourceRef;
        pub fn CGEventCreateKeyboardEvent(
            source: CGEventSourceRef,
            virtual_key: CGKeyCode,
            key_down: bool,
        ) -> CGEventRef;
        pub fn CGEventSetFlags(event: CGEventRef, flags: CGEventFlags);
        pub fn CGEventSetType(event: CGEventRef, event_type: CGEventType);
        pub fn CGEventPost(tap: CGEventTapLocation, event: CGEventRef);
    }

    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        pub fn CFRelease(cf: *const c_void);
    }
}

/// One event of the synthetic paste sequence. `flags_changed` marks the
/// modifier transitions that must be posted as `kCGEventFlagsChanged`
/// instead of keyDown/keyUp.
#[cfg(target_os = "macos")]
struct PasteEvent {
    key: ffi::CGKeyCode,
    down: bool,
    flags: ffi::CGEventFlags,
    flags_changed: bool,
}

#[cfg(target_os = "macos")]
fn paste_events(v_keycode: ffi::CGKeyCode) -> [PasteEvent; 4] {
    use ffi::*;

    // The Cmd transitions must be `flagsChanged` events carrying the
    // resulting modifier state — that is what real hardware produces for a
    // pure modifier press, and it's the only event type Chromium/Electron
    // feeds into its tracked modifier state. Posting Cmd as a plain
    // keyDown (even with the Command flag set on the event) leaves that
    // tracker showing "Command up", so the following V matches neither the
    // Cmd+V accelerator nor plain-text insertion and Electron drops it
    // silently. AppKit reads the V event's own flags and pastes
    // regardless, which is why native apps (TextEdit) worked while every
    // Electron target (Slack, VS Code, Claude, ChatGPT) silently no-op'd
    // — verified empirically on 2026-07-30.
    [
        PasteEvent {
            key: KEYCODE_LEFT_CMD,
            down: true,
            flags: K_CG_EVENT_FLAG_MASK_COMMAND,
            flags_changed: true,
        },
        PasteEvent {
            key: v_keycode,
            down: true,
            flags: K_CG_EVENT_FLAG_MASK_COMMAND,
            flags_changed: false,
        },
        PasteEvent {
            key: v_keycode,
            down: false,
            flags: K_CG_EVENT_FLAG_MASK_COMMAND,
            flags_changed: false,
        },
        PasteEvent {
            key: KEYCODE_LEFT_CMD,
            down: false,
            flags: 0,
            flags_changed: true,
        },
    ]
}

/// Post the four-event Cmd+V sequence to the HID event tap.
///
/// Returns after the events are queued — there's no completion callback,
/// so callers should sleep briefly afterwards to let the target app
/// process the paste before any follow-up (e.g. clipboard restore).
#[cfg(target_os = "macos")]
pub fn send_paste() -> Result<(), String> {
    use ffi::*;

    let v_keycode = crate::keyboard_layout::paste_keycode_v();

    unsafe {
        let source = CGEventSourceCreate(K_CG_EVENT_SOURCE_STATE_HID_SYSTEM_STATE);
        if source.is_null() {
            return Err("CGEventSourceCreate returned null".into());
        }
        let _source_guard = scopeguard::guard(source, |s| CFRelease(s as *const c_void));

        let events = paste_events(v_keycode);

        // Build the four events up front so CFRelease happens after all posts.
        // Posting in a loop that interleaved create → post → release would
        // work, but keeping the events alive for the full sequence matches
        // the pattern CGEventPost's docs show and is easier to reason about.
        let mut guards = Vec::with_capacity(events.len());
        let mut created = Vec::with_capacity(events.len());

        for PasteEvent { key, down, flags, flags_changed } in events {
            let event = CGEventCreateKeyboardEvent(source, key, down);
            if event.is_null() {
                return Err(format!(
                    "CGEventCreateKeyboardEvent(key={}, down={}) returned null",
                    key, down
                ));
            }
            let guard = scopeguard::guard(event, |e| CFRelease(e as *const c_void));
            if flags_changed {
                CGEventSetType(event, K_CG_EVENT_FLAGS_CHANGED);
            }
            // Set flags unconditionally: CGEventCreateKeyboardEvent seeds
            // modifier-key events with its own flag bookkeeping, so the
            // Cmd-up flagsChanged must be explicitly forced to 0.
            CGEventSetFlags(event, flags);
            created.push(event);
            guards.push(guard);
        }

        for event in created {
            CGEventPost(K_CG_HID_EVENT_TAP, event);
        }

        drop(guards);
        Ok(())
    }
}

#[cfg(target_os = "windows")]
mod win {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYBD_EVENT_FLAGS, KEYEVENTF_KEYUP,
        VIRTUAL_KEY,
    };

    pub fn make_key(vk: VIRTUAL_KEY, up: bool) -> INPUT {
        let flags = if up {
            KEYEVENTF_KEYUP
        } else {
            KEYBD_EVENT_FLAGS(0)
        };
        INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: vk,
                    wScan: 0,
                    dwFlags: flags,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        }
    }
}

#[cfg(target_os = "windows")]
pub fn send_paste() -> Result<(), String> {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, VK_CONTROL, VK_V,
    };

    // Four-event Ctrl+V sequence. Matches the macOS CGEvent pattern: the
    // modifier brackets the letter so the target app sees a fully formed
    // accelerator rather than a lone V. `dwExtraInfo` is zero — we're not
    // tagging these as "ours" because no consumer in the paste path needs
    // to distinguish synthetic events from hardware ones.
    let events = [
        win::make_key(VK_CONTROL, false),
        win::make_key(VK_V, false),
        win::make_key(VK_V, true),
        win::make_key(VK_CONTROL, true),
    ];

    unsafe {
        let sent = SendInput(&events, std::mem::size_of::<INPUT>() as i32);
        if sent as usize != events.len() {
            return Err(format!(
                "SendInput delivered {} of {} events — the input desktop may be locked (secure attention sequence) or a higher-integrity window is intercepting.",
                sent,
                events.len()
            ));
        }
    }

    Ok(())
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
pub fn send_paste() -> Result<(), String> {
    Err("synthetic paste is not yet implemented on this platform".into())
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::{ffi::*, paste_events};

    #[test]
    fn cmd_transitions_are_flags_changed_events() {
        let events = paste_events(0);

        // Cmd-down: flagsChanged carrying the Command bit.
        assert_eq!(events[0].key, KEYCODE_LEFT_CMD);
        assert!(events[0].down);
        assert_eq!(events[0].flags, K_CG_EVENT_FLAG_MASK_COMMAND);
        assert!(events[0].flags_changed);

        // V down/up: plain key events with the Command flag.
        assert!(!events[1].flags_changed);
        assert!(!events[2].flags_changed);
        assert_eq!(events[1].flags, K_CG_EVENT_FLAG_MASK_COMMAND);

        // Cmd-up: flagsChanged with all modifiers cleared.
        assert_eq!(events[3].key, KEYCODE_LEFT_CMD);
        assert!(!events[3].down);
        assert_eq!(events[3].flags, 0);
        assert!(events[3].flags_changed);
    }
}
