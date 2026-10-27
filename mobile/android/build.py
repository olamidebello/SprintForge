#!/usr/bin/env python3
"""Build a signed, explicitly development-only SprintForge Android preview."""
import argparse,hashlib,json,os,subprocess,tempfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def build(sdk,output,signing):
    tools=sdk/'build-tools/35.0.0';platform=sdk/'platforms/android-35/android.jar'
    for file in [tools/'aapt',tools/'d8',tools/'zipalign',tools/'apksigner',platform]:
        if not file.is_file():raise ValueError('Install Android platform 35 and build-tools 35.0.0: '+str(file))
    signing.mkdir(parents=True,exist_ok=True,mode=0o700);key=signing/'sprintforge-preview.jks'
    env=dict(os.environ,SPRINTFORGE_PREVIEW_STOREPASS='android')
    def run(argv,**kwargs):return subprocess.run([str(a) for a in argv],check=True,env=env,**kwargs)
    if not key.exists():
        run(['keytool','-genkeypair','-noprompt','-keystore',key,'-storetype','JKS','-alias','preview','-keyalg','RSA','-keysize','3072','-validity','10000','-dname','CN=SprintForge Development Preview','-storepass:env','SPRINTFORGE_PREVIEW_STOREPASS','-keypass:env','SPRINTFORGE_PREVIEW_STOREPASS'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);key.chmod(0o600)
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        temp=Path(td);classes=temp/'classes';classes.mkdir();dex=temp/'dex';dex.mkdir();apk=temp/'unsigned.apk'
        run([tools/'aapt','package','-f','-M',ROOT/'AndroidManifest.xml','-S',ROOT/'res','-I',platform,'-F',apk])
        run(['javac','--release','8','-classpath',platform,'-d',classes,*sorted((ROOT/'src').rglob('*.java'))])
        run([tools/'d8','--min-api','26','--lib',platform,'--output',dex,*sorted(classes.rglob('*.class'))])
        with zipfile.ZipFile(apk,'a',compression=zipfile.ZIP_DEFLATED) as archive:archive.write(dex/'classes.dex','classes.dex')
        aligned=temp/'aligned.apk';run([tools/'zipalign','-f','4',apk,aligned]);target=output/'sprintforge-preview.apk'
        run([tools/'apksigner','sign','--ks',key,'--ks-key-alias','preview','--ks-pass','env:SPRINTFORGE_PREVIEW_STOREPASS','--key-pass','env:SPRINTFORGE_PREVIEW_STOREPASS','--out',target,aligned])
        result=run([tools/'apksigner','verify','--verbose','--print-certs',target],capture_output=True,text=True)
        fingerprint=next(line.split(': ',1)[1] for line in result.stdout.splitlines() if 'certificate SHA-256 digest:' in line)
        manifest=run([tools/'aapt','dump','badging',target],capture_output=True,text=True).stdout
        if "name='com.sprintforge.mobile.preview'" not in manifest or "versionName='0.1.0'" not in manifest:raise ValueError('APK metadata mismatch')
        digest=hashlib.sha256(target.read_bytes()).hexdigest();(output/'sprintforge-preview.apk.sha256').write_text(digest+'  sprintforge-preview.apk\n')
        metadata=dict(name='SprintForge Preview',package='com.sprintforge.mobile.preview',version='0.1.0',version_code=1,min_android=8,channel='development-preview',sha256=digest,size_bytes=target.stat().st_size,signing_certificate_sha256=fingerprint,website='https://sprintforge.netwizzard.tech/',install_url='https://sprintforge.netwizzard.tech/install',native_background_calls=False)
        (output/'release.json').write_text(json.dumps(metadata,indent=2)+'\n');print(json.dumps(metadata))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--sdk',type=Path,default=Path(os.environ.get('ANDROID_HOME','/opt/android-sdk')));parser.add_argument('--output',type=Path,default=ROOT/'output');parser.add_argument('--signing',required=True,type=Path);args=parser.parse_args();build(args.sdk,args.output,args.signing)
