import streamlit as st


class TriggerDispatcher:
    """Debounce + cooldown gate for ROI-triggered announcements.

    Debounce: object must be continuously present in ROI for `debounce` seconds.
    Cooldown: after firing, won't fire again for `cooldown` seconds.
    State is stored in st.session_state to survive Streamlit reruns.
    """

    def __init__(self, debounce: float = 0.5, cooldown: float = 10.0):
        self.debounce = debounce
        self.cooldown = cooldown

    def _state(self) -> dict:
        if "dispatcher_roi_state" not in st.session_state:
            st.session_state.dispatcher_roi_state = {}
        return st.session_state.dispatcher_roi_state

    def on_detected(self, roi_name: str, now: float) -> bool:
        """Call every frame when object is inside roi_name. Returns True when trigger fires."""
        s = self._state()
        if roi_name not in s:
            s[roi_name] = {"first_seen": now, "last_triggered": 0.0}
            return False

        entry = s[roi_name]
        if entry["first_seen"] is None:
            entry["first_seen"] = now

        if now - entry["last_triggered"] < self.cooldown:
            return False

        if now - entry["first_seen"] >= self.debounce:
            entry["last_triggered"] = now
            entry["first_seen"] = None
            return True

        return False

    def on_not_detected(self, roi_name: str):
        """Call every frame when object is NOT inside roi_name (resets debounce)."""
        s = self._state()
        if roi_name in s:
            s[roi_name]["first_seen"] = None

    def cooldown_remaining(self, roi_name: str, now: float) -> float:
        """Seconds left in cooldown for roi_name (0 if not in cooldown)."""
        s = self._state()
        if roi_name not in s:
            return 0.0
        elapsed = now - s[roi_name].get("last_triggered", 0.0)
        return max(0.0, self.cooldown - elapsed)

    def clear(self):
        if "dispatcher_roi_state" in st.session_state:
            st.session_state.dispatcher_roi_state.clear()
