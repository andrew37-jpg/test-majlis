"""Local operations. Never stores backups in source archives."""
import argparse,datetime,os,subprocess,sys
from pathlib import Path
from majlis.config import ROOT

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['backup','inspect-backup']);parser.add_argument('--server',action='store_true');parser.add_argument('--file',type=Path)
    args=parser.parse_args()
    if args.command=='backup':
        folder=ROOT/'data/backups';folder.mkdir(parents=True,exist_ok=True)
        target=args.file or folder/('majlis-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.dump')
        if target.exists():raise SystemExit('Файл уже существует. Выберите другой путь.')
        compose='compose.server.yaml' if args.server else 'compose.local.yaml'
        descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'wb') as output:
            result=subprocess.run(['docker','compose','--env-file',str(ROOT/'.env'),'-f',str(ROOT/compose),'exec','-T','db','pg_dump','-U','majlis','-d','majlis','-Fc'],stdout=output,stderr=subprocess.PIPE,cwd=ROOT)
        if result.returncode:
            print('Резервная копия не завершена. Файл может быть неполным.',file=sys.stderr);return 1
        print('Резервная копия: '+str(target.resolve()))
    else:
        if not args.file or not args.file.is_file():raise SystemExit('Укажите существующий --file.')
        compose='compose.server.yaml' if args.server else 'compose.local.yaml'
        with args.file.open('rb') as source:
            result=subprocess.run(['docker','compose','--env-file',str(ROOT/'.env'),'-f',str(ROOT/compose),'exec','-T','db','pg_restore','--list'],stdin=source,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=ROOT)
        if result.returncode:print('Архив PostgreSQL не прочитан.');return 1
        print('Архив PostgreSQL читается. Это не подтверждает успешное восстановление.')
    return 0
if __name__=='__main__':raise SystemExit(main())
