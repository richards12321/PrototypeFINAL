"""Browser-side text-to-speech helper for Layer 3.

Uses the Web Speech API in the candidate's browser to "speak" the interview
question out loud. This is intentionally browser-side (no Azure call) so:

  1. We don't need a TTS deployment we don't currently have on Capgemini's
     Azure (only chat + transcribe are deployed in CAPSTONE_CONFIG).
  2. There's zero added latency — the browser starts speaking immediately.
  3. There's no API cost per question.

Tradeoff: voice quality depends on the candidate's OS/browser. Edge and
Chrome on Windows/Mac sound good. Safari is decent. Firefox can be flat.

If a `gpt-4o-mini-tts` deployment is ever added to Capstone Azure, swap
the implementation in `speak()` to call that endpoint and stream the
returned MP3 — the public API of this module stays identical.
"""

from __future__ import annotations

import html
import uuid

import streamlit.components.v1 as components


def speak(text: str, *, autoplay: bool = True, voice_hint: str = "en") -> None:
    """Render a hidden HTML component that speaks `text` aloud once.

    A unique key per call ensures Streamlit re-mounts the component every
    rerun, which retriggers playback. Without this the same instance would
    be reused and the audio wouldn't replay.

    Args:
        text: The text to speak. HTML-escaped before injection.
        autoplay: If True, speech starts immediately. If False, only the
            "Replay question" button speaks. Some browsers block autoplay
            without prior user interaction; the button is the safety net.
        voice_hint: Language hint passed to SpeechSynthesisUtterance. Use
            "en" or "en-US" for English voices.
    """
    safe_text = html.escape(text).replace("`", r"\`").replace("\n", " ")
    component_key = f"tts_{uuid.uuid4().hex[:8]}"
    autoplay_js = "speakNow();" if autoplay else "// no autoplay"

    component_html = f"""
    <div id="{component_key}" style="margin: 0; padding: 0;">
      <button
        onclick="speakNow()"
        style="
          background: #f0f2f6;
          border: 1px solid #cbd5e1;
          border-radius: 6px;
          padding: 6px 12px;
          font-size: 13px;
          cursor: pointer;
          color: #334155;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        "
      >
        🔊 Replay question
      </button>
      <span id="{component_key}_status" style="margin-left: 8px; font-size: 12px; color: #64748b;"></span>
    </div>
    <script>
      (function() {{
        const text = `{safe_text}`;
        const statusEl = document.getElementById("{component_key}_status");

        function setStatus(msg) {{
          if (statusEl) statusEl.textContent = msg;
        }}

        function pickVoice() {{
          const voices = window.speechSynthesis.getVoices();
          if (!voices || voices.length === 0) return null;
          // Prefer high-quality English voices when available.
          const preferred = [
            "Google US English", "Microsoft Aria Online (Natural) - English (United States)",
            "Microsoft Jenny Online (Natural) - English (United States)",
            "Samantha", "Alex", "Karen", "Daniel"
          ];
          for (const name of preferred) {{
            const v = voices.find(v => v.name === name);
            if (v) return v;
          }}
          // Fallback: any English voice.
          return voices.find(v => v.lang && v.lang.startsWith("{voice_hint}")) || voices[0];
        }}

        function speakNow() {{
          if (!("speechSynthesis" in window)) {{
            setStatus("Voice playback not supported in this browser.");
            return;
          }}
          window.speechSynthesis.cancel();
          const utter = new SpeechSynthesisUtterance(text);
          const voice = pickVoice();
          if (voice) utter.voice = voice;
          utter.lang = "{voice_hint}";
          utter.rate = 0.95;
          utter.pitch = 1.0;
          utter.onstart = () => setStatus("Speaking...");
          utter.onend = () => setStatus("");
          utter.onerror = () => setStatus("");
          window.speechSynthesis.speak(utter);
        }}

        // Voices load asynchronously in some browsers. If they're not ready,
        // wait for the voiceschanged event before speaking.
        if (window.speechSynthesis.getVoices().length === 0) {{
          window.speechSynthesis.onvoiceschanged = () => {{ {autoplay_js} }};
        }} else {{
          {autoplay_js}
        }}

        // Expose for the replay button (it's defined inline above).
        window.__speakNow_{component_key} = speakNow;
      }})();
    </script>
    """
    components.html(component_html, height=42)
