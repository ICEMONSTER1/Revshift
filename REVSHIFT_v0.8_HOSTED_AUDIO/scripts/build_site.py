#!/usr/bin/env python3
"""Build a self-hosted, openly licensed real-car audio website.
The generated site contains only audio assets that were really downloaded and
validated, and the car UI is populated only with those verified local assets.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / 'audio_sources.json').read_text('utf-8'))
MAX_BYTES = 60 * 1024 * 1024
UA = 'REVSHIFT-Real-Audio-Builder/0.8 (educational noncommercial use; credit in site)' 

# Original recordings are from the specific car named in each source's Wikimedia
# file-description page. The CC-BY / CC-BY-SA license & attribution travel with it.
DETAILS = {
 's2000': dict(region='japan',type='I4',cyl=4,idle=900,redline=9000,gears=6,duration='2:17',engine='F20C · original field recording; exhaust setup not documented'),
 'auris': dict(region='japan',type='V6',cyl=6,idle=900,redline=7600,gears=6,duration='0:14',engine='Auris Andros Trophy 2010 · race-car field recording'),
 'cayman': dict(region='europe',type='F6',cyl=6,idle=900,redline=7200,gears=6,duration='0:33',engine='Cayman S 2006 · flat-six field recording'),
 'gt3rs': dict(region='europe',type='F6',cyl=6,idle=1000,redline=8400,gears=6,duration='0:11',engine='997 GT3 RS · passing race-car field recording'),
 'boxster': dict(region='europe',type='F6',cyl=6,idle=900,redline=7200,gears=6,duration='0:16',engine='Boxster S 987 · in-car acceleration field recording'),
}

def get_binary(url: str, path: Path) -> None:
    if not url.startswith('https://upload.wikimedia.org/'):
        raise ValueError('Only approved Wikimedia file host is allowed')
    err = None
    for retry in range(3):
        try:
            req = Request(url, headers={'User-Agent': UA, 'Accept': 'audio/mpeg,audio/ogg,audio/wav,*/*;q=0.1'})
            with urlopen(req, timeout=60) as response:
                if response.status != 200:
                    raise ValueError(f'HTTP {response.status}')
                content_type = response.headers.get('content-type','').lower()
                if 'text/html' in content_type or 'application/json' in content_type:
                    raise ValueError(f'Unexpected response Content-Type {content_type}')
                with path.open('wb') as out:
                    downloaded = 0
                    while True:
                        chunk = response.read(1024*128)
                        if not chunk: break
                        downloaded += len(chunk)
                        if downloaded > MAX_BYTES: raise ValueError('Too large')
                        out.write(chunk)
            if downloaded < 4000: raise ValueError('Too small / not a real recording')
            return
        except (HTTPError,URLError,TimeoutError,ValueError) as e:
            err = e
            path.unlink(missing_ok=True)
            if retry < 2: time.sleep((retry+1)*2)
    raise RuntimeError(f'{type(err).__name__}: {err}')

def is_mp3(path: Path) -> bool:
    try:
        with path.open('rb') as fh:
            h = fh.read(4)
        return h[:3] == b'ID3' or (h[:1] == b'\xff' and h[1:2] and (h[1] & 0xE0) == 0xE0)
    except FileNotFoundError:
        return False

def check_mp3(path:Path) -> bool:
    if not path.exists() or path.stat().st_size < 4000 or not is_mp3(path): return False
    ffprobe = shutil.which('ffprobe')
    if not ffprobe: return True
    proc = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1',str(path)],capture_output=True, text=True)
    if proc.returncode: return False
    try:return float(proc.stdout.strip())>=1
    except (ValueError,TypeError):return False

def transcode_url(source):
    uri = urlparse(source['direct_url'])
    parts = uri.path.split('/')
    # Wikimedia convention /wikipedia/commons/0/0d/File.ogg
    return ('https://upload.wikimedia.org/wikipedia/commons/transcoded/' +
            '/'.join(parts[-3:-1]) + '/' + parts[-1] + '/' + parts[-1] + '.mp3')

def source_is_audio(path: Path) -> bool:
    with path.open('rb') as f: h=f.read(12)
    return h.startswith(b'OggS') or h.startswith(b'RIFF') and h[8:12] == b'WAVE'

def get_or_create_mp3(item, output:Path, preloaded: Path|None):
    if preloaded:
        existing = preloaded / f"{item['id']}.mp3"
        if check_mp3(existing):
            shutil.copy2(existing,output)
            return 'preloaded MP3'
    # Prefer Wikimedia's transcoded MP3 for use on iPhone Safari.
    err1 = None
    try:
        get_binary(transcode_url(item),output)
        if not check_mp3(output): raise ValueError('Transcoded file was not a valid MP3')
        return 'Wikimedia MP3 transcode'
    except Exception as e:
        err1 = str(e)
        output.unlink(missing_ok=True)
    # If Wikimedia's MP3 transcode is not available, fetch the original and
    # convert using ffmpeg. Never pass OGG directly to the iPhone browser.
    if not shutil.which('ffmpeg'):
        raise RuntimeError('MP3 transcode unavailable and ffmpeg is not installed; '+str(err1))
    with tempfile.TemporaryDirectory() as tmp:
        original = Path(tmp)/('original.'+item['local_filename'].split('.')[-1])
        get_binary(item['direct_url'],original)
        if not source_is_audio(original): raise RuntimeError('Not Ogg/WAV audio bytes')
        subprocess.run(['ffmpeg','-nostdin','-y','-hide_banner','-loglevel','error','-i',str(original),'-vn','-ac','2','-ar','44100','-b:a','160k',str(output)],check=True,timeout=120)
    if not check_mp3(output): raise RuntimeError('Converted MP3 is invalid')
    return 'original WAV/OGG converted with ffmpeg'

def build(dest:Path, preloaded:Path|None=None):
    sounds = dest / 'sounds'
    sounds.mkdir(parents=True, exist_ok=True)
    available=[]
    for item in CATALOG:
        if item['id'] not in DETAILS: continue
        output = sounds / f"{item['id']}.mp3"
        print(f"Preparing {item['brand']} {item['model']} ...",flush=True)
        try:
            if check_mp3(output): how='already validated'
            else:
                output.unlink(missing_ok=True)
                how = get_or_create_mp3(item,output,preloaded)
            print(f'  OK: {output.stat().st_size/1024/1024:.2f} MB ({how})',flush=True)
            details=DETAILS[item['id']]
            available.append(dict(id=item['id'],brand=item['brand'],model=item['model'],region=details['region'],engine=details['engine'],type=details['type'],cyl=details['cyl'],idle=details['idle'],redline=details['redline'],gears=details['gears'],filename=item['filename'],author=item['author'],license=item['license'],licenseUrl={'CC BY 3.0':'https://creativecommons.org/licenses/by/3.0/','CC BY-SA 3.0':'https://creativecommons.org/licenses/by-sa/3.0/'}[item['license']],duration=details['duration'],ext='mp3',directUrl=f"./sounds/{item['id']}.mp3",sourcePage=item['source_page']))
        except Exception as e:
            output.unlink(missing_ok=True)
            print(f'  SKIPPED: {e}',flush=True)
    if not available:
        raise SystemExit('No verified MP3 audio was downloaded. Build aborted: will not publish a silent car UI.')

    template = (ROOT/'template.html').read_text('utf-8')
    # Only show the exact vehicles whose real recordings were successfully fetched.
    template, replaced = re.subn(r'const PROFILES = \[.*?\];\s*const filePage=', 'const PROFILES = '+json.dumps(available,ensure_ascii=False,separators=(',',':'))+';\nconst filePage=', template,count=1,flags=re.S)
    if replaced!=1: raise RuntimeError('Could not patch sound catalog')
    # All playback comes from our own same-origin MP3 assets. No arbitrary
    # fallbacks and no HTTP 200 HTML response masquerading as MP3.
    template, replaced = re.subn(r'getSources\(\)\{const p=this\.p;return \[.*?\];\}', 'getSources(){return [{name:"VERIFIED HOSTED MP3",url:`./sounds/${this.p.id}.mp3`}] ;}',template,count=1,flags=re.S)
    if replaced!=1: raise RuntimeError('Could not patch sound source')
    template=template.replace("const filePage=p=>`https://commons.wikimedia.org/wiki/File:${encodeURIComponent(p.filename).replace(/%2F/g,'/')}`;", "const filePage=p=>p.sourcePage;")
    template=template.replace("byId('downloadStatus').textContent=localObjects.has(profile.id)?'มีไฟล์ที่เลือกไว้สำหรับรุ่นนี้แล้ว':'ยังไม่ได้เลือกไฟล์ในเครื่อง · จะลองโฟลเดอร์ sounds/ และ URL ออนไลน์';", "byId('downloadStatus').textContent='เสียง MP3 จากเว็บเดียวกัน · ไม่ต้องอัปโหลดไฟล์';")
    template=template.replace('REAL FREE SOUND LAB','REAL SOUND · HOSTED MP3')
    template=template.replace('RECORDED AUDIO','HOSTED REAL AUDIO')
    template=template.replace('ต้องมีอินเทอร์เน็ต','เสียง MP3 อยู่ในเว็บ · ไม่ต้องโหลดเอง')
    template=template.replace('const localAudioUrl=p=>`./sounds/${p.id}.${p.ext}`;', 'const localAudioUrl=p=>`./sounds/${p.id}.mp3`;')
    template=template.replace("byId('downloadOriginal').href=profile.directUrl;", "byId('downloadOriginal').href=profile.sourcePage;")
    # No upload-interface prompts: the creator bundles all sound assets at build.
    template=template.replace('</style>', "\n/* v0.8: All real audio hosted with site; no manual sound upload in car. */\n#audioRecovery,.bank-panel{display:none!important}\n</style>",1)
    template=template.replace('href="/" aria-label="REV SHIFT home"','href="./" aria-label="REV SHIFT home"')
    template=template.replace('<title>REV//SHIFT — Interactive Engine Lab</title>','<title>REV//SHIFT v0.8 — XPENG Cockpit · Hosted Real Sound</title>')
    # Hide PHONE SYNC on static hosting because this site has no WebSocket relay.
    template=template.replace("if(phoneRoom){setMode('phone');}else{setMode('demo');}","if(phoneRoom){setMode('phone');}else{setMode('demo');}\nif(!location.hostname.includes('localhost') && location.hostname.endsWith('github.io')){ document.querySelectorAll('[data-hud-input=\"dashboard\"]').forEach(b=>{b.disabled=true;b.title='PHONE SYNC requires an HTTPS WebSocket server'}); }")
    (dest/'index.html').write_text(template,'utf-8')
    (dest/'.nojekyll').write_text('','utf-8')
    (sounds/'manifest.json').write_text(json.dumps(available,ensure_ascii=False,indent=2),'utf-8')
    page = '''<!doctype html><meta charset="utf-8"><title>Audio credits - REV//SHIFT</title><style>body{background:#0a1418;color:#e6f8ea;font:16px/1.6 system-ui;max-width:850px;margin:5vh auto;padding:0 20px}a{color:#b6fc60}</style><h1>Real Engine Audio Credits</h1><p>Original field recordings supplied under Creative Commons licenses. Files may be resampled/converted to MP3 for browser compatibility. These recordings are short samples, not full interactive engine recordings. No affiliation with any automaker.</p><ul>'''
    for p in available:
        page+=f'<li><b>{p["brand"]} {p["model"]}</b> — {p["author"]} — {p["license"]} — <a href="{p["sourcePage"]}">original / attribution</a> — <a href="{p["licenseUrl"]}">license</a></li>'
    page+='</ul><p><a href="./">Back to cockpit</a></p>'
    (dest/'CREDITS.html').write_text(page,'utf-8')
    print(f'Build completed with {len(available)} real MP3 recordings; {len(CATALOG)-len(available)} hidden.')

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--out',type=Path,default=ROOT/'dist')
    ap.add_argument('--preloaded',type=Path,default=None,help='Optional already-licensed MP3 source files for development')
    args=ap.parse_args()
    build(args.out,args.preloaded)
