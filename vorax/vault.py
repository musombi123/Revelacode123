"""Mirror GitHub Home V-Lang and independently verified media to a private HF Dataset."""
from __future__ import annotations
import hashlib,io,json,os,re,subprocess,wave
from pathlib import Path
MAX_BYTES=40*1024*1024
class VaultError(RuntimeError): pass
def _client():
 token=os.getenv("HF_VAULT_TOKEN","").strip()
 if not token: raise VaultError("HF_VAULT_TOKEN is not configured")
 from huggingface_hub import HfApi
 repo=os.getenv("HF_VAULT_REPO","Musombi/revelacode-vorax-vault")
 if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",repo): raise VaultError("Invalid Vault repo ID")
 api=HfApi(token=token); api.create_repo(repo_id=repo,repo_type="dataset",private=True,exist_ok=True)
 return api,repo
def _verify(path):
 if not path.is_file() or not 0<path.stat().st_size<=MAX_BYTES: raise VaultError("Missing, empty or oversized artifact")
 ext=path.suffix.lower()
 if ext==".png":
  from PIL import Image,ImageStat
  with Image.open(path) as im: im.verify()
  with Image.open(path) as im:
   im.load()
   if im.width<16 or im.height<16 or all(a==b for a,b in ImageStat.Stat(im.convert("RGB")).extrema): raise VaultError("Blank or invalid PNG")
   detail={"width":im.width,"height":im.height}
  kind="png"
 elif ext==".wav":
  with wave.open(str(path),"rb") as w:
   frames,rate,channels=w.getnframes(),w.getframerate(),w.getnchannels()
   w.rewind(); sample=w.readframes(min(frames,rate*10))
   if frames<=0 or rate<=0 or channels<=0 or not sample or not any(sample): raise VaultError("Silent or invalid WAV")
   detail={"duration_seconds":round(frames/rate,3),"sample_rate":rate,"channels":channels}
  kind="wav"
 elif ext==".mp4":
  p=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-count_frames","-show_entries","stream=nb_read_frames,width,height,duration","-of","json",str(path)],capture_output=True,text=True,timeout=20,check=True)
  streams=json.loads(p.stdout).get("streams",[])
  if not streams or int(streams[0].get("nb_read_frames") or 0)<=10: raise VaultError("MP4 has no video or <=10 frames")
  detail={k:streams[0].get(k) for k in ("nb_read_frames","width","height","duration")}; kind="mp4"
 else: raise VaultError("Only PNG/WAV/MP4 artifacts are accepted")
 return {"kind":kind,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"size_bytes":path.stat().st_size,**detail}
def mirror_home_and_artifacts(records,artifact_root):
 api,repo=_client()
 from .vlang import dump_record
 methods=artifacts=total=0; proofs=[]
 for record in records:
  name=str(record.get("id",""))
  if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}",name): continue
  data=dump_record(record).encode()
  api.upload_file(path_or_fileobj=io.BytesIO(data),path_in_repo=f"vlang/{name}.vlang",repo_id=repo,repo_type="dataset",commit_message="Mirror verified Home V-Lang method")
  methods+=1; total+=len(data)
 root=Path(artifact_root).resolve()
 for path in sorted(root.iterdir()) if root.exists() else []:
  if path.suffix.lower() not in {".png",".wav",".mp4"}: continue
  try: proof=_verify(path)
  except Exception: continue
  if root not in path.resolve().parents: continue
  with path.open("rb") as f: api.upload_file(path_or_fileobj=f,path_in_repo=f"artifacts/{proof['sha256']}.{proof['kind']}",repo_id=repo,repo_type="dataset",commit_message="Mirror independently verified VORAX artifact")
  artifacts+=1; total+=proof["size_bytes"]; proofs.append(proof)
 manifest={"vault_repo":repo,"repo_type":"dataset","methods_mirrored":methods,"artifacts_verified_and_mirrored":artifacts,"bytes_uploaded":total,"artifact_proofs":proofs}
 api.upload_file(path_or_fileobj=io.BytesIO(json.dumps(manifest,indent=2).encode()),path_in_repo="reports/vault/latest.json",repo_id=repo,repo_type="dataset",commit_message="Update VORAX Vault manifest")
 return manifest
