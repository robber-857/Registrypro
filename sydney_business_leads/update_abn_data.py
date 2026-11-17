"""Download both official weekly snapshot parts, then atomically replace the index."""
import json
import shutil
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from abn_bulk import ROOT, build_index

CATALOG='https://data.gov.au/data/api/3/action/package_show?id=5bd7fcab-e315-42cb-8daf-50b7efc2027e'

def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    with urllib.request.urlopen(CATALOG,timeout=60) as response:
        package=json.load(response)
    resources=[r for r in package['result']['resources'] if r['format']=='ZIP']
    if not package.get('success') or len(resources)!=2:
        raise ValueError('Official catalog must contain both snapshot parts')
    def download(resource):
        target=ROOT/resource['url'].rsplit('/',1)[-1]
        size=int(resource.get('size') or 0)
        marker=target.with_suffix('.source.json')
        identity={k:resource.get(k) for k in ('url','last_modified','size')}
        if target.exists() and marker.exists() and json.loads(marker.read_text())==identity and target.stat().st_size==size:
            print('Reusing verified download:',target.name,flush=True)
            return
        temporary=target.with_suffix('.download')
        print('Downloading:',target.name,flush=True)
        with urllib.request.urlopen(resource['url'],timeout=180) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response,output,1024*1024)
        if not size or temporary.stat().st_size!=size:
            raise ValueError('Incomplete download: '+target.name)
        temporary.replace(target)
        marker.write_text(json.dumps(identity))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(download,resources))
    (ROOT/'package.json').write_text(json.dumps(package))
    print(json.dumps(build_index(),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
