#!/usr/bin/env python3
"""
Re-burn word-level karaoke subtitles onto existing generated videos in output/
"""
import os
import sys
import glob
import json
import re
import shutil
import subprocess

FONTS_DIR = "/home/kodar/face/resource/fonts"

def srt_to_ass(srt_path: str, ass_path: str):
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    blocks = re.split(r'\n\s*\n', content.strip())
    
    header = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Montserrat-Black,48,&H00FFFFFF,&H0000D7FF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,3,10,0,2,60,60,540,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    events = []
    word_cues = []
    
    def to_ass_time(sec):
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f'{h}:{m:02d}:{s:05.2f}'

    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) < 3:
            continue
        m = re.match(r'(\d+):(\d+):(\d+)[,\.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,\.](\d+)', lines[1])
        if not m:
            continue
        start_sec = int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3)) + int(m.group(4))/1000.0
        end_sec = int(m.group(5))*3600 + int(m.group(6))*60 + int(m.group(7)) + int(m.group(8))/1000.0
        text = ' '.join(lines[2:])
        words = text.split()
        if not words:
            continue
        
        dur_cs = int(round((end_sec - start_sec) * 100))
        total_chars = sum(len(w) for w in words)
        dur_sec = max(0.1, end_sec - start_sec)
        
        karaoke_text = ''
        rem_cs = dur_cs
        curr_t = start_sec
        for i, w in enumerate(words):
            if i == len(words) - 1:
                w_cs = max(1, rem_cs)
            else:
                w_cs = max(1, int(round(dur_cs * (len(w) / total_chars))))
                rem_cs -= w_cs
            karaoke_text += f'{{\\\\k{w_cs}}}{w} '
            
            w_dur = dur_sec * (len(w) / total_chars)
            word_cues.append({
                "word": w,
                "start": round(curr_t, 3),
                "end": round(curr_t + w_dur, 3)
            })
            curr_t += w_dur
        
        start_str = to_ass_time(start_sec)
        end_str = to_ass_time(end_sec)
        events.append(f'Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{karaoke_text.strip()}')

    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(header + '\n'.join(events) + '\n')
        
    return word_cues


def process_video(meta_path: str) -> bool:
    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
    except Exception as e:
        print(f"Error reading {meta_path}: {e}")
        return False

    task_id = meta.get("task_id")
    dest_video = meta.get("file_path")
    if not dest_video or not os.path.exists(dest_video):
        print(f"  [Skip] Destination video not found: {dest_video}")
        return False

    task_dir = os.path.join("storage/tasks", task_id) if task_id else None
    srt_path = os.path.join(task_dir, "subtitle.srt") if task_dir else None
    if not srt_path or not os.path.exists(srt_path):
        print(f"  [Skip] No subtitle.srt for task {task_id}")
        return False

    source_video = os.path.join(task_dir, "combined.mp4") if task_dir else None
    if not source_video or not os.path.exists(source_video):
        source_video = dest_video

    ass_path = os.path.join(task_dir, "karaoke.ass")
    word_cues = srt_to_ass(srt_path, ass_path)

    # Save karaoke.json as well
    if task_dir:
        with open(os.path.join(task_dir, "karaoke.json"), "w", encoding="utf-8") as kf:
            json.dump(word_cues, kf, indent=2)

    tmp_out = dest_video + ".reburned.mp4"
    print(f"  Re-burning: {meta.get('title', 'Video')} ({len(word_cues)} cues)...")

    cmd = [
        "ffmpeg", "-y",
        "-i", source_video,
        "-vf", f"ass={ass_path}:fontsdir={FONTS_DIR}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-c:a", "copy",
        tmp_out
    ]

    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if res.returncode != 0 or not os.path.exists(tmp_out) or os.path.getsize(tmp_out) < 100000:
        print(f"  [Error] FFmpeg failed: {res.stderr.decode()[-300:]}")
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        return False

    # Replace destination video
    shutil.move(tmp_out, dest_video)
    if task_dir:
        shutil.copy2(dest_video, os.path.join(task_dir, "final.mp4"))

    # Update metadata file size
    new_size_mb = round(os.path.getsize(dest_video) / (1024 * 1024), 2)
    meta["file_size_mb"] = new_size_mb
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)

    print(f"  ✓ Success: {dest_video} ({new_size_mb} MB)")
    return True


def main():
    meta_files = sorted(glob.glob("output/**/metadata.json", recursive=True))
    print(f"Found {len(meta_files)} video archives in output/")
    success_count = 0
    for idx, mf in enumerate(meta_files, 1):
        print(f"\n[{idx}/{len(meta_files)}] Processing {mf}...")
        if process_video(mf):
            success_count += 1
    print(f"\n🎉 Finished re-burning subtitles on {success_count}/{len(meta_files)} videos!")


if __name__ == "__main__":
    main()
