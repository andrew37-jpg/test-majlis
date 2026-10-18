import json
from datetime import date, datetime
from psycopg.types.json import Jsonb
from psycopg.errors import UniqueViolation
from .domain import STAGES, STAGE_MAP, Problem, https_url

def serial(value):
    if isinstance(value, (date, datetime)): return value.isoformat()
    if isinstance(value, dict): return {k: serial(v) for k,v in value.items()}
    if isinstance(value, list): return [serial(v) for v in value]
    return value

def ensure_user(db, user, started=False):
    db.execute('''INSERT INTO users(telegram_id,first_name,bot_started) VALUES (%s,%s,%s)
        ON CONFLICT(telegram_id) DO UPDATE SET first_name=CASE WHEN excluded.first_name<>'' THEN excluded.first_name ELSE users.first_name END,
        bot_started=users.bot_started OR excluded.bot_started,updated_at=now()''',
        (user['id'], str(user.get('first_name',''))[:100], started))

def get_order(db, oid, lock=False):
    row = db.execute('SELECT * FROM orders WHERE id=%s' + (' FOR UPDATE' if lock else ''), (oid,)).fetchone()
    if not row: raise Problem(404, 'Заказ не найден.')
    return row

def public_order(row, events):
    data = serial(row)
    data['eta'] = data['eta'] or ''
    data['stage'] = STAGE_MAP[row['status']]
    data['events'] = serial(events)
    return data

def order_json(db, oid):
    row = get_order(db, oid)
    events = db.execute('SELECT id,order_id,status,note,source,created_at FROM events WHERE order_id=%s ORDER BY id', (oid,)).fetchall()
    return public_order(row, events)

def audit(db, actor, action, before, after):
    db.execute('INSERT INTO audit_log(actor_id,action,order_id,before_data,after_data) VALUES (%s,%s,%s,%s,%s)',
               (actor, action, after['id'], Jsonb(serial(before)), Jsonb(serial(after))))

def bootstrap(db, user, cfg, before=None, q='', bucket='all'):
    if bucket not in ('all','active','delivered'):raise Problem(400,'Неизвестный фильтр заказов.')
    params=[];clauses=[]
    if user['role']!='manager':clauses.append('customer_id=%s');params.append(user['id'])
    scope=' WHERE '+ ' AND '.join(clauses) if clauses else ''
    summary=db.execute("SELECT count(*) AS total,count(*) FILTER (WHERE status<>'delivered') AS active,count(*) FILTER (WHERE status='delivered') AS delivered FROM orders"+scope,params).fetchone()
    if bucket=='active':clauses.append("status<>'delivered'")
    elif bucket=='delivered':clauses.append("status='delivered'")
    q=q.strip()
    if q:
        needle='%'+q.replace(chr(92),chr(92)*2).replace('%',chr(92)+'%').replace('_',chr(92)+'_')+'%'
        fields=['number','item','shipment']+(['customer_name','customer_id::text'] if user['role']=='manager' else [])
        clauses.append('('+' OR '.join(field+' ILIKE %s' for field in fields)+')');params.extend([needle]*len(fields))
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    summary['matching']=db.execute('SELECT count(*) AS n FROM orders'+where,params).fetchone()['n']
    if before:clauses.append('id<%s');params.append(before)
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    rows=db.execute('SELECT * FROM orders'+where+' ORDER BY id DESC LIMIT 101',params).fetchall()
    more=len(rows)>100;rows=rows[:100]
    ids=[row['id'] for row in rows]
    events=db.execute('SELECT id,order_id,status,note,source,created_at FROM events WHERE order_id=ANY(%s) ORDER BY id',(ids,)).fetchall() if ids else []
    grouped={oid:[] for oid in ids}
    for event in events:grouped[event['order_id']].append(event)
    result={'user':user,'demo':False,'stages':[{'code':s[0],'title':s[1],'description':s[2]} for s in STAGES],
        'orders':[public_order(row,grouped[row['id']]) for row in rows], 'summary':summary,
        'updated_at':datetime.now().astimezone().isoformat(), 'next_cursor':rows[-1]['id'] if more else None,
        'links':{'catalog':cfg.catalog_url,'support':cfg.support_url},
        'integrations':{'cargo':'not_connected','delivery':'not_connected','website':'not_connected','payments':'manual_links' if cfg.payment_domains else 'not_connected'}}
    if user['role']=='manager':result['notifications']=serial(db.execute('SELECT id,state,attempts,last_error FROM outbox ORDER BY id DESC LIMIT 30').fetchall())
    return result

def create_order(db, data, actor):
    ensure_user(db, {'id':data.customer_id})
    try:
        with db.transaction():
            row=db.execute('''INSERT INTO orders(number,customer_id,customer_name,item,amount,shipment,status,eta)
                VALUES (%s,%s,%s,%s,%s,%s,'confirmed',%s) RETURNING *''',
                (data.number,data.customer_id,data.customer_name,data.item,data.amount,data.shipment,data.eta)).fetchone()
    except UniqueViolation: raise Problem(409,'Такой номер заказа уже существует.') from None
    db.execute("INSERT INTO events(order_id,status,note,source,actor_id) VALUES (%s,'confirmed','Заказ зарегистрирован','manager',%s)",(row['id'],actor))
    audit(db,actor,'create',{},row)
    return order_json(db,row['id'])

def check_revision(row, version):
    if row['version'] != version: raise Problem(409,'Заказ уже изменён. Обновите кабинет и повторите действие.')

def change_status(db, oid, status, note, source, actor=None, version=None):
    if status not in STAGE_MAP: raise Problem(400,'Неизвестный этап доставки.')
    old=get_order(db,oid,lock=True)
    if version is not None: check_revision(old,version)
    if old['status']==status: return order_json(db,oid)
    row=db.execute('UPDATE orders SET status=%s,version=version+1,updated_at=now() WHERE id=%s RETURNING *',(status,oid)).fetchone()
    event=db.execute('INSERT INTO events(order_id,status,note,source,actor_id) VALUES (%s,%s,%s,%s,%s) RETURNING id',(oid,status,note,source,actor)).fetchone()
    stage=STAGE_MAP[status]
    text=f"Заказ {old['number']}\n{stage['title']}\n{stage['description']}"
    db.execute('INSERT INTO outbox(event_id,customer_id,text) VALUES (%s,%s,%s)',(event['id'],old['customer_id'],text))
    audit(db,actor,'status',old,row)
    return order_json(db,oid)

def update_details(db,data,actor):
    old=get_order(db,data.order_id,lock=True);check_revision(old,data.version)
    ensure_user(db,{'id':data.customer_id})
    # Never deliver notifications queued for the previous owner after reassignment.
    if old['customer_id']!=data.customer_id:
        db.execute("UPDATE outbox SET state='failed',last_error='owner_changed' WHERE event_id IN (SELECT id FROM events WHERE order_id=%s) AND state IN ('pending','sending')",(data.order_id,))
    row=db.execute('''UPDATE orders SET customer_id=%s,shipment=%s,eta=%s,eta_confirmed=%s,version=version+1,updated_at=now()
        WHERE id=%s RETURNING *''',(data.customer_id,data.shipment,data.eta,data.eta_confirmed and bool(data.eta),data.order_id)).fetchone()
    audit(db,actor,'details',old,row)
    return order_json(db,data.order_id)

def update_payment(db,data,actor,cfg):
    if not cfg.payment_domains: raise Problem(503,'Платёжный сервис не настроен.')
    url=https_url(data.url,cfg.payment_domains)
    old=get_order(db,data.order_id,lock=True);check_revision(old,data.version)
    row=db.execute('UPDATE orders SET payment_url=%s,version=version+1,updated_at=now() WHERE id=%s RETURNING *',(url,data.order_id)).fetchone()
    audit(db,actor,'payment_link',old,row)
    return order_json(db,data.order_id)

def integration_event(db,data):
    if data.status not in STAGE_MAP: raise Problem(400,'Неизвестный этап доставки.')
    receipt=db.execute('INSERT INTO integration_events(external_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING external_id',(data.event_id,)).fetchone()
    if not receipt:return {'duplicate':True,'updated':0}
    orders=db.execute('SELECT id FROM orders WHERE shipment=%s ORDER BY id FOR UPDATE',(data.shipment,)).fetchall()
    if not orders: raise Problem(404,'Партия не найдена.')
    for row in orders: change_status(db,row['id'],data.status,'Обновление от внешней системы','integration')
    return {'duplicate':False,'updated':len(orders)}

def rate_limit(db, user_id):
    row=db.execute('''INSERT INTO rate_limits(telegram_id,window_start,requests) VALUES (%s,now(),1)
        ON CONFLICT(telegram_id) DO UPDATE SET
        requests=CASE WHEN rate_limits.window_start<now()-interval '1 minute' THEN 1 ELSE rate_limits.requests+1 END,
        window_start=CASE WHEN rate_limits.window_start<now()-interval '1 minute' THEN now() ELSE rate_limits.window_start END
        RETURNING requests''',(user_id,)).fetchone()
    if row['requests']>120: raise Problem(429,'Слишком много запросов. Повторите через минуту.')
