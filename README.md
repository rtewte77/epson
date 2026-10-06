# photo-pilot

Scan a whole glass full of old prints at once with an **Epson Perfection** flatbed. photo-pilot
finds each photo, cuts it out, straightens it, skips any photo it already scanned,
and uploads it to **Immich**, optionally with the date it was taken and an album name.

Runs on **Windows** and **Linux Mint** (and other Linux distributions or macOS).

```
 ┌──────── scanner glass ────────┐          Immich
 │  ┌────┐   ┌──────┐            │     ┌──────────────┐
 │  │ 1  │   │  2   │  ┌───┐     │ ──▶ │ 1  2  3  4   │  album "Summer 1979"
 │  └────┘   └──────┘  │ 3 │     │     │ dated 1979   │
 │     ┌───────┐       └───┘     │     └──────────────┘
 │     │   4   │                 │
 └───────────────────────────────┘
```

## Supported scanners

Any Epson Perfection flatbed that has an Epson driver for your system, for example
V850 Pro, V800, V750 Pro, V700, V600, V550, V500, V370, 4990, 4490, 4870, 3200 and 2400. Other
brands also work if Windows (WIA) or Linux (SANE) can see them.

Prints only: scanning goes through the glass (flatbed), not the film/slide holder.

## Windows

1. Install the scanner driver for your model from Epson's support site. Look for
   **Epson Scan** or **Epson Scan 2**: it also installs the Windows (WIA) driver photo-pilot uses.
2. Install Python 3 from <https://www.python.org/downloads/>, with **"Add python.exe to PATH"** ticked.
3. Download this project and double-click **`windows\install.bat`**. At the end it should list
   your scanner.
4. Open **`windows\Scan photos.bat`** in Notepad and fill in your Immich address and API key
   (see [Immich API key](#immich-api-key)).
5. Double-click **`Scan photos.bat`** whenever you want to scan.

If the direct scan doesn't work with your model or driver, use Epson's own software instead:
scan the **whole glass** with auto-crop off into `Pictures\Epson Scan`, and keep
**`Watch Epson Scan folder.bat`** running. It handles each new file as soon as it appears.

## Linux Mint

```sh
git clone <this repo> && cd epson
./linux/install-linux-mint.sh     # installs SANE + photo-pilot, then lists scanners
```

Log out and back in once. If `photo-pilot devices` lists nothing, install Epson's Linux driver.
On Epson's support site, choose your model and **Linux** as the operating system:

* **V850, V800, V600, V550, V370 and newer**: *Epson Scan 2* (`epsonscan2` `.deb` bundle,
  plus the non-free plugin if your model needs it). Run its `install.sh`.
* **Older models (V700, V750, 4990, 4490, …)**: often work out of the box through SANE's
  `epson2` backend. Otherwise install *Image Scan! for Linux* (`iscan`) and its plugin for your model.

Check with `scanimage -L`, then run:

```sh
export IMMICH_URL=http://192.168.1.10:2283
export IMMICH_API_KEY=your-key
photo-pilot scan
```

## Scanning

```
$ photo-pilot scan
Connected to Immich at http://192.168.1.10:2283
Put photos face down on the glass with ~1 cm between them, then close the lid.

[date=scan time] > d 1979-08           <- photos below are from August 1979
[date=1979-08-01] > a Summer 1979      <- ...and go into this album
[date=1979-08-01, album=Summer 1979] > <Enter>
Scanning...
Scan done in 48s: found 4 photo(s), saved 4
  -> Immich: 20261006-153012-01.jpg
  ...
  -> added 4 to album 'Summer 1979'
[date=1979-08-01, album=Summer 1979] >   <- swap the photos, press Enter again
```

| at the prompt | does |
|---|---|
| Enter | scan, split, save, upload |
| `d 1978` / `d 1978-07` / `d 1978-07-14` | date for the next photos (`d` alone: use scan time) |
| `a Holiday 78` | Immich album for the next photos (`a` alone: no album) |
| `u` | retry failed uploads (e.g. while the Immich server was offline) |
| `q` | quit |

Tips for good results:

* Leave about **1 cm between photos** so photos that touch aren't cut out as one.
* Photos with **white borders on the white lid** are the hardest case. A sheet of **black paper**
  laid over the photos (under the lid) makes detection near perfect. Also try `--sensitivity 1.5`.
* Check `<out>/beds/*-detected.jpg`: each scan shows numbered red boxes for what was found.
* 600 dpi is plenty for normal prints. Use `--dpi 1200` for small prints you may want to enlarge.
* Place photos upright where you can. Turning one sideways in Immich takes one click.

### Other commands

```sh
photo-pilot devices                       # which scanners are visible
photo-pilot import scans/                 # split existing full-glass scans (files or a folder)
photo-pilot watch "~/Pictures/Epson Scan" # process each new scan Epson's software saves there
photo-pilot upload                        # retry anything not uploaded yet
photo-pilot scan --no-upload              # only save JPEGs locally
```

All commands accept `--out DIR` (default `./photos`), `--album`, `--date`, `--no-upload`,
`--sensitivity` and `--min-area`. Run `photo-pilot <command> --help` for every option.

## Immich API key

In Immich: avatar → **Account Settings → API Keys → New API Key**. The key needs at least
`asset.upload`, `album.read`, `album.create` and `albumAsset.create`. The URL is the address
you use in the browser, e.g. `http://192.168.1.10:2283`.

Once the photos are uploaded, Immich's own **face recognition** and **smart search**
work on them as well ("beach", "birthday cake", a person's name).

## How it works

* **Finding photos**: the colour of the lid is measured at the edges of the scan. Anything that
  differs from it, or shows a sharp edge, is treated as photo. Each blob becomes one photo, and its
  tilted rectangle is straightened and trimmed slightly so no lid shows at the edges
  (`photopilot/detect.py`).
* **Duplicates**: each photo gets a perceptual hash (in all four orientations). A re-scan of a
  photo that's already done is skipped even if it's turned around. Immich also refuses
  byte-identical uploads.
* **Dates**: written into the JPEG's EXIF `DateTimeOriginal`, so the photo sits at the right place
  on Immich's timeline.
* **Safe to stop**: progress lives in `<out>/.photo-pilot.json`. Photos that failed to upload are
  retried the next time the program runs.
* **Scanner access**: WIA on Windows (`pywin32`), SANE `scanimage` on Linux and macOS
  (`photopilot/scanner.py`).

## Development

```sh
pip install -e '.[test]'
pytest
```

The tests build synthetic scans (white and black lid, photos with and without white borders,
tilted, with dust). They run the full import → Immich upload → album → duplicate flow against a
fake Immich server, and the Windows WIA path against a fake driver.
