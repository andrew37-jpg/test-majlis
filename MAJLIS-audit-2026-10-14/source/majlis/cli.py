import argparse,signal,sys
from .database import Database

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['migrate','api','bot'])
    args=parser.parse_args()
    try:
        if args.command=='migrate':
            db=Database();db.open()
            try:db.migrate();print('Миграции PostgreSQL применены.')
            finally:db.close()
        elif args.command=='api':
            import uvicorn
            from .config import settings
            uvicorn.run('majlis.api:app',host='0.0.0.0',port=settings().port,access_log=False,log_level='warning',proxy_headers=False)
        else:
            from .bot import run
            import threading
            stop=threading.Event()
            for name in ('SIGTERM','SIGINT'):
                if hasattr(signal,name):signal.signal(getattr(signal,name),lambda *_:stop.set())
            run(stop)
    except KeyboardInterrupt:return 0
    except Exception as exc:
        # Do not print validation reprs / exception URLs that can include credentials.
        print('Ошибка компонента: '+type(exc).__name__+'. Проверьте локальные настройки и доступность сервисов.',file=sys.stderr)
        return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
