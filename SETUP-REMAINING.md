# Remaining actions for you

1. Add your own `ASSEMBLYAI_API_KEY` and `GROQ_API_KEY` to `~/Interview Assistant/.env` locally. Leave `GROQ_MODEL=openai/gpt-oss-20b` or explicitly choose a model available to your account. Check provider credits/spend limits. Do not send keys in chat.
2. Double-click `~/Desktop/Start Interview Assistant.command`. If macOS requests permission for Terminal/Python microphone access, decide and approve it yourself; restart afterward if needed.
3. Configure/reuse a Multi-Output Device containing your listening output and BlackHole 2ch, following README.md. Keep the meeting microphone on your physical mic. No audio settings were changed for you.
4. Select the physical microphone and BlackHole in the app. Run each local Test separately, confirming its meter moves and you can hear system playback. Core Audio inputs were not visible during development, so this step is required.
5. Run one short session (around 10–20 seconds), speak an interviewer question through the system source and verify both labelled transcripts plus one suggested answer. Press Stop, confirm both streams disconnect, and inspect AssemblyAI/Groq usage. Real cloud transcription/generation and physical capture remain unverified until this check succeeds.
