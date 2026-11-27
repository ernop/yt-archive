# yt-archive

yt-archive downloads YouTube videos, and videos posted on X, to your own computer. With each video it saves an MP3 of the sound track, a picture of every shot for videos up to 30 minutes, and, when asked, a transcript of the speech. You use it from a web page that the same computer serves.

## Adding videos

- Paste one or more links into the box on the archive page; each video joins the download queue. YouTube video links (watch, `youtu.be`, Shorts, live, embed), bare YouTube video IDs, and links to tweets with a video all work.
- A video already archived is not downloaded again, and a video already queued is not added twice.
- Pasting a creator, playlist or X profile link opens the picker, which lists its videos to choose from (next section).
- **+ ytarchive** is a bookmark button: drag it from the archive page to the bookmarks bar. Clicked on a YouTube video or a tweet, it queues that video without leaving the page. Clicked on a creator, playlist or X profile page, it opens the picker.
- A pasted video link that was playing in a playlist adds a link to choose more videos from that playlist.
- Other programs on the same computer can add videos too.

## Choosing from a creator, playlist or X profile

The picker lists the videos of a YouTube creator, a playlist or an X profile. Nothing downloads until videos are chosen.

- It takes a creator's homepage or @handle, a playlist link, or an X profile link. Your Watch later, Liked videos, Liked music and private playlists work too. An X profile is listed newest first.
- A creator's list has tabs: Videos, Shorts, Live, All, and Playlists. Each playlist opens in the picker.
- Filters: text in the title or video ID, length, upload age, and "hide saved and queued". Sort: as listed, newest, oldest, longest, shortest, most viewed, or title.
- Each row shows whether the video is already saved or queued, or why it cannot be chosen: live now, not ready yet, restricted, or unavailable.
- Select by clicking rows, shift-clicking a range, **Select all shown**, **Select first N**, or with keys: J and K move, X selects, O opens the video on YouTube or X. The selection stays through tabs, filters, pages and page reloads.
- **Go one by one** shows each video large, to choose (Y), skip (N) or go back (B).
- **Get selected** or **Get all shown** adds them to the download queue, skipping videos already saved or queued. More than 50 at once asks first.
- The 60 most recently used lists are kept until **Refresh list** is pressed, so opening one again does not ask YouTube or X again.

## What each video gets

- **The video file** at the highest resolution offered, with its thumbnail, title, channel, description and upload date.
- **Shot pictures.** yt-archive checks the video twice a second and keeps a frame whenever it looks different from the last frame kept. Videos over 30 minutes skip shots by default; **Make shots** on the video page makes them on request.
- **A framesheet**: all of a video's shot pictures laid out together in one image. Past 250 shots it is split into several images.
- **MP3 audio** of the sound track. A video without sound gets none.
- **Lyrics**, when a program on the computer sends them for the video. They are kept beside the MP3.

## The archive page

- **Download queue**, updated live. The video being worked on shows its steps (download, shots, MP3), percent done, download speed and time left. Waiting videos can be removed one at a time or all at once. Failed ones show their error, with **Retry** and **Dismiss** (or **Retry all failed** and **Dismiss all failed** when more than one has failed). Videos are worked on one at a time; the queue keeps going with the page closed and continues after the program restarts.
- **Archive list**, one line per video: thumbnail, title, creator, length, number of shots, upload date, download date, and where the first request for it came from (paste box, bookmark button, picker, command line, or another program).
- **Search** finds videos whose title, channel, description, video ID, transcript, English translation or speaker names contain the words typed.
- **Sort** by download date (newest or oldest first), upload date, title, or channel.
- The heading shows how many videos there are, the disk space they take, and the free space left on the disk.
- **Update N videos.** When videos archived by an earlier version lack something the current version makes, or have shots found the earlier way, a bar says how many and what is missing, and this button queues the missing work.
- **Creator pages.** Each creator name links to a page of that creator's archived videos, with the same search and sorting. When the YouTube channel is known, the page links to it and to the picker for the creator's other videos.

## The video page

- **Facts** on one line: creator, length, resolution, file size, number of shots, upload date, download date, and a link to the original on YouTube or X. The description follows, folded.
- **View bar.** Controls at the top of the video page show or hide the shot marks on the seek bar (**Frame jumps**), **Split view**, **Transcript**, **Overlap timeline**, **MP3 audio**, **Framesheets**, and **Shot images**. A **Save** switch applies changes either to **All videos** or only to **This video**, with buttons to promote a video's layout to the default or return to defaults.
- **Player.** Each shot is a mark on the seek bar, and the picture of the shot playing is outlined in red. Clicking a shot picture plays from that moment; PageUp and PageDown then step from shot to shot.
- **Keys.** S saves the frame on screen as a full-size PNG beside the MP3. T enlarges the video from 72% to 86% of the window height. R turns repeat on or off: the video starts again each time it ends. B switches between the original audio and the English dub when a dub exists. C cycles captions. ? lists every key. The player offers to resume where you stopped, and remembers speed, volume, captions and the enlarged view.
- **MP3.** It plays on the page and downloads as a file named after the artist and title. **Open MP3 folder** opens its folder on the computer. **Create MP3 audio** makes one for a video that has none. Lyrics, when present, download beside it.
- **The framesheet and every shot picture** are in collapsible sections on the page (multi-sheet framesheets and large shot grids start closed), or **Make shots** when shots were skipped for a video over 30 minutes.
- **Redo.** **Rebuild shots** remakes the shot pictures and framesheet from the local file. **Download again** downloads the video again, deletes its transcript and English files, makes a new MP3, and rebuilds shots if the video has them or is 30 minutes or shorter.

## Transcripts

- **Transcribe audio…** on the video page runs local speech recognition on this computer, only when asked. The dialog offers two modes: single-stream Whisper (large-v3 by default, with model, language, beam-size and silence-filtering choices) and multi-speaker mode, which labels up to 8 speakers and transcribes overlapping speech across 25 European languages.
- Transcripts hold spoken words only. Labels such as [music] or (applause) are removed.
- The transcript panel highlights every line and word being spoken and can scroll with playback inside the panel (**Auto-scroll** switches that on or off). Clicking a word or a line plays from there. The panel searches the transcript, filters by speaker or overlaps, shows all of it or only the current caption, and has three text sizes.
- **Speakers and overlaps.** Clicking a line's speaker label names its speaker; lines with the same name form a group that can be renamed or merged. When a transcript has two or more speakers on a wide screen, **Split view** places the video on the left (60%), the scrollable transcript lines on the right (40%), and the live color-coded speaker subtitles plus full-width speaker timeline and overview bar underneath both (**Stacked view** returns to full-width video). The timeline positions words along the time axis at their spoken timestamps, marks simultaneous speech with overlap durations, provides previous/next overlap jumps, and toggles live speaker subtitles docked, overlaid on the video as a compact time-aligned overlap strip, or hidden. Overlapped words in the transcript are underlined in the other speaker's color, with inline notes at mid-sentence interjections.
- **Captions.** The transcript shows as captions in the player (C, or the CC button) and downloads as a WebVTT subtitle file. **Cover burned-in captions** draws captions on an opaque black band near the bottom of the video.
- **Re-transcribe…** replaces the transcript.

## English versions

- **Make English version…** on the video page translates a video into English on request. It reuses an existing transcript or runs local speech recognition first, translates the speech into English captions and transcript, and can generate a multi-speaker English voiceover dub mixed over lowered original audio.
- The dialog shows the configured translation model and an estimated build time, with options to skip the voiceover dub or specify the source language.
- In the player, B switches between Original audio and the English dub, and C cycles English captions, Original captions, and off.
- The transcript panel switches between English, Original, and Side-by-side views, downloads English WebVTT, ASS, and SRT subtitle files, and lets you edit or rename speakers.
- **Download bilingual MKV** and **Open in VLC** package the original video, both audio tracks, styled and plain subtitles, and translated chapters into one MKV file without re-encoding the video.
- **Rebuild English version…** replaces the English translation and dub.

## Accounts and limits

- YouTube downloads use your YouTube login from Firefox. Creators and public playlists are listed without it; Watch later, Liked videos, Liked music and private playlists are listed with it.
- Tweets are downloaded without an X account. A tweet that X shows only to signed-in readers is fetched again with your X login from Firefox. X profiles are always listed with that login.
- Hourly limits: 150 YouTube downloads, 60 tweet downloads, 30 tweet downloads with your X login, 500 requests to YouTube for lists, and 60 requests to X for lists. A download past its limit waits in the queue, which says when it starts.

Setup: [docs/setup.md](docs/setup.md). Command line, file layout, and how each part works: [AGENTS.md](AGENTS.md).

## License

MIT; see [LICENSE](LICENSE).
