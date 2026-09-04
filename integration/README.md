# integration

End-to-end integration: connecting the AI subsystem (laptop), navigation, and firmware (ESP32) into a working system, plus demo scripts and bring-up guides.

## Status

Bring-up phase: the **laptop side of the laptop ↔ ESP32 link** lives in
`communication/` (protocol helpers, transport, `LaptopLink`, and the
`demo_laptop` bench console — see its README).  AI, navigation and the
state machine are not implemented yet; they will reuse `LaptopLink` later.

Run the laptop-side tests from the repository root:

```bash
python -m unittest discover -s tests -v
```