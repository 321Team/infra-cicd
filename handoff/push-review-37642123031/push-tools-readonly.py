import datetime,json,os,pathlib,stat,subprocess
out={'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'host':os.uname().nodename,'uid':os.getuid(),'home_mode':oct(stat.S_IMODE(pathlib.Path.home().stat().st_mode)),'runtime_push':'NOT_EXECUTED'}
for name,cmd in [('skopeo_version',['/usr/bin/skopeo','--version']),('skopeo_rpm',['rpm','-q','skopeo']),('skopeo_rpm_verify',['rpm','-V','skopeo'])]:
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
        out[name]={'rc':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
    except FileNotFoundError:
        out[name]={'status':'NOT_INSTALLED'}
for sub,required in [('copy',['--preserve-digests','--dest-authfile','--dest-tls-verify']),('inspect',['--raw','--authfile','--tls-verify'])]:
    try:
        r=subprocess.run(['/usr/bin/skopeo',sub,'--help'],capture_output=True,text=True,timeout=20)
        out[sub+'_options']={'rc':r.returncode,'found':{x:x in r.stdout for x in required}}
    except FileNotFoundError:
        out[sub+'_options']={'status':'NOT_INSTALLED'}
print(json.dumps(out,indent=2))
