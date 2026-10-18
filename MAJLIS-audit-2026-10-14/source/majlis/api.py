import hashlib,hmac,json,time,logging
from contextlib import asynccontextmanager
from typing import Annotated
from fastapi import FastAPI,Request,Depends,Query
from fastapi.responses import JSONResponse,FileResponse
from fastapi.exceptions import RequestValidationError
from psycopg import OperationalError
from .config import settings,ROOT
from .database import Database
from .domain import Problem,validate_init_data
from .schemas import NewOrder,Status,Details,Payment,Integration
from . import services

@asynccontextmanager
async def lifespan(app):
    db=Database();db.open();app.state.db=db
    try: yield
    finally: db.close()

app=FastAPI(title='MAJLIS API',version='0.4.0',lifespan=lifespan,docs_url=None,redoc_url=None)

@app.middleware('http')
async def boundaries(request,call_next):
    # Buffer only a bounded API body. Sensitive request headers/body are never logged.
    if request.method=='POST':
        length=request.headers.get('content-length','')
        if not length.isdigit() or not 0<int(length)<=65536: return JSONResponse({'error':'Размер запроса превышен.'},413)
        if not request.headers.get('content-type','').startswith('application/json'): return JSONResponse({'error':'Требуется JSON.'},415)
        body=await request.body()
        if len(body)>65536:return JSONResponse({'error':'Размер запроса превышен.'},413)
    response=await call_next(request)
    response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
        'Content-Security-Policy':"default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; connect-src 'self'; img-src 'self' data: https://*.telegram.org; base-uri 'none'; form-action 'self'"})
    return response

@app.exception_handler(Problem)
async def problem(request,exc):return JSONResponse({'error':exc.message},exc.status)
@app.exception_handler(RequestValidationError)
async def invalid(request,exc):return JSONResponse({'error':'Проверьте поля формы и обновите кабинет.'},422)
@app.exception_handler(Exception)
async def failed(request,exc):
    logging.getLogger('majlis').error('API failure: %s',type(exc).__name__)
    return JSONResponse({'error':'Не удалось выполнить запрос. Повторите позже.'},500)

def identity(request:Request):
    raw=request.headers.get('X-Telegram-Init-Data','')
    if len(raw)>8192: raise Problem(401,'Некорректные данные входа.')
    try:user=validate_init_data(raw,settings().bot_token.get_secret_value())
    except (ValueError,TypeError):raise Problem(401,'Некорректные данные Telegram.') from None
    user={k:user[k] for k in ('id','first_name','last_name','username') if k in user}
    user['role']='manager' if user['id'] in settings().managers else 'buyer'
    # Limits commit independently so failed mutations cannot reset the counter.
    with request.app.state.db.connection() as db:services.rate_limit(db,user['id']);services.ensure_user(db,user)
    return user

def manager(user=Depends(identity)):
    if user['role']!='manager':raise Problem(403,'Действие доступно только менеджеру.')
    return user

@app.get('/health')
def health():return {'ok':True,'mode':'live','instance_id':settings().instance_id}
@app.get('/ready')
def ready(request:Request):
    with request.app.state.db.connection() as db:
        db.execute('SELECT 1')
        pending=db.execute('SELECT count(*) AS total FROM schema_migrations').fetchone()['total']
    if not pending:raise Problem(503,'Миграции не применены.')
    return {'ok':True,'database':'postgresql'}
@app.get('/')
def index():return FileResponse(ROOT/'web/index.html',media_type='text/html')
@app.get('/app.js')
def script():return FileResponse(ROOT/'web/app.js',media_type='text/javascript')
@app.get('/ui.js')
def ui_script():return FileResponse(ROOT/'web/ui.js',media_type='text/javascript')
@app.get('/style.css')
def style():return FileResponse(ROOT/'web/style.css',media_type='text/css')
@app.get('/api/bootstrap')
def bootstrap(request:Request,before:Annotated[int|None,Query(gt=0)]=None,q:Annotated[str,Query(max_length=100)]='',bucket:Annotated[str,Query(pattern='^(all|active|delivered)$')]='all',user=Depends(identity)):
    with request.app.state.db.connection() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        return services.bootstrap(db,user,settings(),before,q,bucket)
@app.get('/api/orders/{order_id}')
def read_order(order_id:int,request:Request,user=Depends(identity)):
    if not 0<order_id<=9223372036854775807:raise Problem(404,'Заказ не найден.')
    with request.app.state.db.connection() as db:
        row=services.get_order(db,order_id,lock=True)
        if user['role']!='manager' and row['customer_id']!=user['id']:raise Problem(404,'Заказ не найден.')
        return services.order_json(db,order_id)
@app.post('/api/orders',status_code=201)
def create(data:NewOrder,request:Request,user=Depends(manager)):
    with request.app.state.db.connection() as db:return services.create_order(db,data,user['id'])
@app.post('/api/orders/status')
def status(data:Status,request:Request,user=Depends(manager)):
    with request.app.state.db.connection() as db:return services.change_status(db,data.order_id,data.status,data.note,'manager',user['id'],data.version)
@app.post('/api/orders/details')
def details(data:Details,request:Request,user=Depends(manager)):
    with request.app.state.db.connection() as db:return services.update_details(db,data,user['id'])
@app.post('/api/orders/payment')
def payment(data:Payment,request:Request,user=Depends(manager)):
    with request.app.state.db.connection() as db:return services.update_payment(db,data,user['id'],settings())
@app.post('/api/integrations/events')
async def integration(request:Request):
    secret=settings().integration_secret.get_secret_value()
    if len(secret)<32:raise Problem(503,'Внешние интеграции пока не подключены.')
    stamp=request.headers.get('X-Event-Timestamp','');signature=request.headers.get('X-Event-Signature','');raw=await request.body()
    try:
        if abs(time.time()-int(stamp))>300:raise ValueError()
    except ValueError:raise Problem(401,'Подпись события истекла.') from None
    expected=hmac.new(secret.encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,signature):raise Problem(401,'Подпись события не подтверждена.')
    try:data=Integration.model_validate_json(raw)
    except ValueError:raise Problem(422,'Некорректное событие.') from None
    # Offload sync PostgreSQL transactions from the async event loop.
    from starlette.concurrency import run_in_threadpool
    def apply():
        with request.app.state.db.connection() as db:return services.integration_event(db,data)
    return await run_in_threadpool(apply)
