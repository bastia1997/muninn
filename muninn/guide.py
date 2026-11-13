"""Text of the Guide tab. Lines starting with '# ' are headings, '- ' are bullet points."""

GUIDE = r"""# What it does
Records your calls (your microphone, the call audio, screens or windows), then transcribes them with Nvidia Nemotron 3.5 on DeepInfra, separates the voices and lets you name each speaker by listening to a few clips. The result is a folder with a subtitled video and a transcript ready to give to an AI. On top of that, Live captions show on screen, almost in real time, what is being said.

# First start
- Settings → paste the DeepInfra key (deepinfra.com → API Keys), press Check.
- Type your name: it will be shown instead of «Me» (the label for you).
- Press «Save settings» (red seal) in the top bar.
- Audio compression: Opus at 24-32 kbps is the best choice for voice (about 11-14 MB per hour). MP3 if you want compatibility, FLAC/WAV only to send the audio without any loss.

# Recording a call
- Audio tab: tick the microphone (label «Me») and the headphones/speakers output (label «Call») with «Several people (call)» ticked. The level bars must move when someone speaks.
- Not sure which device is the right one? «Test» on a microphone records 4 seconds and plays them back; on an output it plays a chime and checks that it arrives (if you hear it in your call headphones, that is the right one). «Show levels» turns on all the bars: speak or play some audio and see which one moves.
- Video tab (optional): tick the screen where Teams is. It is the safest choice; single windows sometimes come out black.
- Video tab: on the right there is a live preview of the screen or window you select, exactly as it will be recorded. If the preview is black with «Direct window», a warning appears: use «Screen area» or the whole screen.
- Press «Start recording». At the end of the call press «Stop and save»: the program does everything by itself.
- Use headphones: with speakers the microphone also picks up the others and the text gets duplicated.
- Video compression (Video tab): «H.265 Intel Quick Sync» makes small files with almost no load on the processor; «H.264» is the most compatible. «Medium» quality and «Up to 1080p» resolution are fine for meetings and slides.

# Naming the speakers
- When the transcription is finished the Speakers window opens: for each voice press ▶ to hear 5 seconds where only that voice speaks.
- Type or choose the name (the program remembers the ones already used) and press «Apply names»: they replace the placeholders in transcript, subtitles and video.
- If two voices are the same person give them the same name. If you don't know who it is, leave it empty.
- «Later» closes without changing anything: reopen it from the Transcript tab with «Name the speakers…».
- Calls longer than one hour are split into chunks: the program links the voices of the same person across chunks by itself.

# Processing files you already have
- «Process files…» → choose one or more files (Teams recordings, MP4, MP3, M4A…).
- For each one a folder is created as for recordings, then the names window opens.
- Here all voices, including yours, are on the same track.

# Live captions and Notary
- Press «Live captions»: it uses the same sources ticked in the Audio tab and also works without recording.
- Captions appear at the bottom (drag them wherever you want, mouse wheel = size); on the right the Notary window shows everything that was said, line by line, with time and speaker.
- Captions and Notary are not visible in screen sharing or in recordings.
- Delay usually 1-2 seconds: the sentence being spoken is updated while you talk and becomes final at the first pause (half a second). Confirmed words turn white, words the model may still correct stay grey.
- In the top line of the captions «response ~N s» shows how fast DeepInfra is answering: if it goes up to 4-8 s the service is overloaded, not your PC.
- The Notary is saved automatically in recordings\live\notary_date_time.txt. Voices are not separated here: you only see «Me» and «Call».
- Cost: about 1-3 cents per hour.
- «Real time EN» (next to «Live captions»): true streaming, the text appears while people speak (about half a second of delay). It works ONLY in English: use it for calls in English, for other languages use «Live captions». Same sources, same Notary window (file notary_…_EN.txt). Cost about 6 cents per hour per source.

# What you find in the folder
- video_….mp4 — video with audio and built-in subtitles (in VLC: Subtitle menu).
- transcript.md — the conversation in order, with times and names: this is the one to give to the AI.
- transcript.json — the same thing in a structured format.
- speakers.json — who is who (Interlocutore N → name).
- data\ — technical material: audio tracks, .srt, API responses.

# The buttons at the top
- Process files… — transcribes files you already have.
- Transcribe again… — redoes everything on a folder already recorded (e.g. after a network error or a program update). It costs as much as the first time.
- Open folder — opens the last recording or the main folder.
- Transcript tab: «Name the speakers…» and «Apply edited speakers.json…».

# Tray icon and language
- Muninn does not take up the taskbar: it lives in the icon next to the clock (if you don't see it, open the ^ arrow of hidden icons).
- The X and «minimise» only hide the window: recording and captions keep going. Click the icon to reopen it; right-click for Start/Stop recording, Live captions and Quit.
- If you start Muninn again from the Desktop, the window already open comes back.
- Interface language: Settings → Language (italiano, English, français, Deutsch, español), then restart. Transcripts and generated files do not change: the text stays in the spoken language and file names stay in Italian.

# Indicative costs (DeepInfra)
- Transcription with separated voices: about $0.12 per hour of audio per track.
- Live captions: about 1-3 cents per hour.
- In the Transcript tab the lines with ⏱ tell how long each step took.

# Common problems
- «Invalid DeepInfra key» or «credit used up»: check key and credit on deepinfra.com.
- Level bar not moving: you chose the wrong device; press Refresh and choose the one you are really using.
- Black video: record the whole screen instead of the window.
- Chunks «not transcribed»: no network or timeout; use «Transcribe again…».
- Error details: grey messages in the Transcript tab and the error.log file in the program folder.

# Privacy
- The audio is sent to DeepInfra, which states that it does not store it on disk or use it to train models.
- For work recordings check that it is allowed and inform the participants.
"""
