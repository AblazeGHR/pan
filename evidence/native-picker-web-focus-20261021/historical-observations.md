# Earlier 0.4-second observation (raw output no longer retained)

Source: this conversation's functions.exec tool output, before the final harness run reused `mouse-final/result.json`. This is a transcription of observed values, **not reconstructed raw JSON**.

- Isolated port: 59217.
- Before native button mouse click: owned browser HWND 5703984 / PID 6864 was foreground.
- Button mouse event: GetLastInputInfo advanced to 2525359.
- HTTP native-picker entry: time 1792565004.1958673; helper-start observation: 1792565004.2161791, same browser foreground / same last-input tick.
- Helper PID 32692; real dialog HWND 2034320, owner HWND 9046294, ordinary non-TopMost.
- After the harness's initial 0.4-second settling interval, foreground was still the owned browser HWND 5703984; therefore `focused=false` at that sample.
- The harness asserted immediately and did not collect the activation call's return or a later focus sample. Do not classify this as proven persistent `foreground-denied`.
- During cleanup the later HTTP-end observation saw another Edge HWND 394370 / PID 29716 and a new last-input tick 2531640. That later input does not identify what happened at the earlier sample.
- Helpers exited, owned server thread stopped and port closed according to the cleanup result read before that file was overwritten.

The final uninstrumented run recorded both early and later samples plus completed results. Its successful outcome does not erase this earlier short-interval observation. The report explicitly leaves intermittent / deployed-site acceptance unresolved.
