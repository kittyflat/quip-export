# quip-export

A Python script to bulk export all your Quip documents to local Markdown files, with images downloaded and links rewritten to point locally.

## Features

- Recursively exports all folders (private + shared)
- Saves documents as `.md`, preserving your Quip folder structure
- Downloads images locally and rewrites links — no dependency on Quip servers
- Skips already-exported files, so it's safe to re-run
- No external dependencies — pure Python stdlib

## Requirements

- Python 3.10+
- A Quip API token — get yours at [quip.com/dev/token](https://quip.com/dev/token)

## Setup

Add your token to `~/.secrets` (or wherever you manage environment variables):

```bash
export QUIP_TOKEN=your_token_here
```

Make sure that file is sourced in your shell config (`~/.zshrc` or `~/.bashrc`):

```bash
source ~/.secrets
```

## Usage

```bash
# Export everything to ~/quip-export (default)
python3 quip_export.py

# Export to a custom folder
python3 quip_export.py --output ~/Documents/quip-backup

# Export a specific Quip folder only
python3 quip_export.py --folder FOLDER_ID
```

## Output structure

```
~/quip-export/
├── My Notes/
│   ├── Project Ideas.md
│   ├── Meeting Notes.md
│   └── images/
│       └── diagram.png
├── Shared/
│   ├── Team Roadmap.md
│   └── images/
└── ...
```

## Re-running

The script skips any `.md` file that already exists, so you can run it again to pick up new or updated documents without re-exporting everything.
