# Pair your iPhone with Interview Assistant

## On the Mac

1. Connect the Mac and iPhone to the **same trusted Wi-Fi**. Keep Interview Assistant open on the Mac.
2. Set your interview context, then open **Phone companion** and click **Pair Phone**.
3. A QR code, clickable local URL, alternative short code and expiry countdown appear. The invitation lasts **two minutes**.
4. Scan the QR code using the iPhone Camera and open its link in Safari.
5. On the Mac, check the device name, local address and six-digit comparison code against the phone. Click **Approve Device** only for your own request. Choose **Reject** for an unexpected request.

The QR code is generated on your Mac, without an external QR service. No forwarding, tunnel, hosting, subscription, certificate installation or payment details are needed.

## On the iPhone

After scanning, Safari requests approval automatically. Wait for approval on the Mac. If scanning is unavailable, type the displayed **base URL** (including its port) into Safari and enter the eight-character pairing code.

Once approved, you can view current answer suggestions and the latest interviewer question, request written answers during a live session authorised on the Mac, upload an existing screenshot for text extraction on the Mac, and use local mock practice. Practice uses the setup selected when pairing; a running live session uses its existing shared context. Pair again after changing the setup if you want a new context for offline phone practice.

The Mac remains responsible for live microphone/system audio, session authorisation, duration limits, UGX 0 safeguards and **STOP ALL**. Pairing itself makes no provider requests. The phone cannot start or authorise a live session. Closing the laptop's controlling tab stops live capture under the existing rules.

## Disconnect or revoke

The Mac shows the approved device name, its address, connection status and remaining access time. A temporary Wi-Fi interruption reconnects automatically while the existing credential remains valid. Updates resume from a current snapshot containing separate interviewer and microphone transcripts and the current AI answer; answer submissions are never automatically retried.

Access expires after **two hours**. Use **Revoke Device** on the Mac to invalidate credentials immediately and close the companion listener. Pairing again replaces the previous invitation and device. Restarting the backend also revokes access. Revocation prevents future requests; it cannot erase information the phone has already displayed or copied.

## If the phone cannot connect

- **No active private network address:** connect the Mac to Wi-Fi with a private IPv4 address. IPv6-only, public-address-only, VPN-only and inactive interfaces are not supported for pairing.
- **Several addresses:** click **Refresh network addresses**, choose the Wi-Fi interface and click **Pair Phone** again. Wi-Fi is preferred automatically; Ethernet is also available if it reaches the same phone network.
- **Selected address no longer available:** Wi-Fi or the Mac's address changed. Refresh addresses and create a new invitation; the previous URL may no longer be reachable.
- **Listener could not open:** the app could not bind a listener on that local address. Check the network connection and Mac firewall. No successful pairing link is returned when binding fails.
- **Safari cannot open a ready URL:** check both devices' Wi-Fi, the exact address and port, Mac firewall permissions, and any local-network permission prompt. Guest Wi-Fi/client isolation, corporate network policies and VPN routing can block device-to-device connections even on the same Wi-Fi. The Mac cannot determine those router restrictions from its own connection alone.
- **Invalid or expired code:** create a new invitation on the Mac. Each invitation can be claimed once. Five failed attempts trigger a one-minute cooldown.
- **Approval expired or rejected:** request a new invitation and compare the codes again.
- **Disconnected after revocation/restart:** pair again. Safari cannot restore revoked credentials.

Do not configure router port forwarding or expose this service to the internet.

## Security and HTTPS

The primary Mac application stays on loopback. Only clicking **Pair Phone** opens a separate companion listener, bound to one selected private IPv4 address and an automatically chosen port. It accepts private source addresses in that interface's subnet, checks Host and write Origin, ignores forwarded client addresses, and exposes only companion assets and routes. Pairing management, profile administration, live authorisation, audio control and shutdown routes are unavailable on it. Provider keys are never sent to the phone.

Invitation, pending-approval and approved-device credentials are separate, randomly generated secrets. A short pairing code is rate limited and still requires explicit Mac approval. Device credentials stay in Safari session storage rather than URL parameters. Pairing credentials and interview data are not written to access logs. Keep links and codes private.

**Local HTTP is authenticated but not encrypted.** Use a trusted private Wi-Fi network. HTTP cannot protect credentials or interview content from a compromised router, network observer or active attacker. Local restrictions do not provide the transport confidentiality of HTTPS, and a compromised device on the Wi-Fi remains a risk. There is no automatic tunnel, third-party hosting, UPnP or public listener.

Basic Safari page loading, same-origin requests, live answer delivery, written questions, file uploads and session storage do not require a secure context. Browser **screen capture (`getDisplayMedia`)** and direct browser **microphone/camera capture (`getUserMedia`)** do require one. An HTTP LAN IP does not count as trusted localhost. The phone companion does not capture live microphone/system audio; those remain on the Mac. Its screen-capture button is hidden when the browser does not expose that API. HTTPS alone also cannot add an API unsupported by iPhone Safari.

The simplest option for a screenshot question is to take a screenshot on the iPhone and upload that image, or use the Mac's localhost screen-capture feature. If encrypted LAN transport or an available secure-context-only browser feature is required, it needs a separate private-LAN HTTPS setup with a certificate trusted by the iPhone. This application does not configure that setup automatically, and its former external forwarding setting is no longer used. No HTTPS setup is necessary for the ordinary pairing flow above.

References: [MDN secure contexts](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts), [screen capture requirements](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia), [microphone/camera requirements](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).

## Verification status

Automated tests cover local URLs and QR encoding, expired/invalid/single-use invitations, rate limits, Mac approval/rejection, device expiry/revocation, shared context, live answer updates using mocked providers, reconnection, multiple/unavailable interfaces, listener binding failures, and rejected public/off-subnet/Host/Origin/control-route requests. They make no paid provider requests.

A separate real TCP check on this Mac's private Wi-Fi address verified page loading, QR generation, approval/authentication, local practice, control-route restrictions and listener shutdown after revocation. This is a Mac-to-Mac-network-address check, **not an iPhone test**.

**Physical iPhone camera scanning, Safari pairing, firewall prompts, Wi-Fi isolation and phone sleep/reconnection have not been verified on an actual iPhone.** Follow the steps above to perform that final device check; do not treat automated tests as proof of physical phone pairing.


## Live speaker and answer display

The phone has **INTERVIEWER** (system audio), **YOU** (physical microphone) and **SUGGESTED ANSWER** (AI output) sections. Raw questions and your spoken responses never populate the AI answer card. Partial transcripts update immediately, and streamed spoken-answer text is labelled as an unvalidated AI draft until the complete response passes validation. Supporting evidence, code and detail are collapsed beneath the spoken response.

Updates use an authenticated same-origin streaming connection rather than the previous 1.5-second state poll. Brief disconnections reconnect to the current Mac state and do not stop or reset the laptop session. Reconnection retries only the local companion connection, never a provider request. Device revocation also ends the update stream.

Likely duplicate speech across system audio and microphone is hidden from YOU and from model conversation context while retaining original source metadata in the session. This is a conservative text/time heuristic, not diarization or acoustic echo cancellation. It can miss echoes or suppress a genuine rapid repetition. Use headphones when possible to reduce speaker bleed, and confirm that the Mac's microphone selector is your physical microphone rather than a virtual system-audio device.

Open **Latency diagnostics** on either device to see capture, transcript, question and generation stage times. The phone also measures its local received-to-render interval. See [LIVE-PIPELINE.md](LIVE-PIPELINE.md) for implementation limits, tests and explicitly labelled local measurements.


## Groq failures and Waiting status

The phone now shows **Generating a suggested answer** as soon as generation begins. A Groq failure remains visible after cleanup and reconnection. Expand **Latency diagnostics → Last Groq failure · safe diagnostic details** to see its category, HTTP status (or no status for local/network failures), exception type and safe request settings. Non-billing/access/rate-limit failures pause suggestions while transcription continues; strict provider limits still stop live services. No failed provider request is automatically retried. Stop and correct the reported cause before authorising a new session.

The production backend saves only the last safe failure in `.runtime/last-groq-error.json`. This file is owner-only, contains no interview/CV text or API keys, and is not a session archive. Prior missing session records cannot be reconstructed from it. See [REGRESSION-INVESTIGATION.md](REGRESSION-INVESTIGATION.md) for the SDK compatibility correction and limits of the historical investigation.
