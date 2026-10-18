"""Validated generic CSV import, dry run by default. Old DB adapter needs its schema."""
import argparse,csv,json
from storage import connect,initialize,create_order,change_status
from domain import Problem,STAGE_MAP,positive_int

def import_csv(path,db_path,apply=False,demo=False):
 initialize(db_path,demo)
 with open(path,encoding='utf-8-sig',newline='') as f:
  reader=csv.DictReader(f)
  if not {'number','customer_id','customer_name','item','amount','status'}.issubset(reader.fieldnames or []): raise Problem(400,'CSV: не хватает обязательных колонок.')
  rows=list(reader)
  if len(rows)>50000: raise Problem(400,'CSV: максимум 50000 строк за импорт.')
 seen=set();count=0;skipped=0;db=connect(db_path)
 try:
  db.execute('BEGIN IMMEDIATE')
  for row in rows:
   if row['number'] in seen: raise Problem(400,'CSV: повтор номера заказа внутри файла.')
   seen.add(row['number'])
   if row['status'] not in STAGE_MAP: raise Problem(400,'CSV: неизвестный статус.')
   row['amount']=positive_int(row['amount'],'amount');row['customer_id']=positive_int(row['customer_id'],'customer_id')
   row.setdefault('eta','');row.setdefault('shipment','')
   old=db.execute('SELECT * FROM orders WHERE number=?',(row['number'],)).fetchone()
   if old:
    keys=['number','customer_id','customer_name','item','amount','status','eta','shipment']
    if any(old[k]!=row[k] for k in keys): raise Problem(409,'CSV: существующий заказ отличается. Нужна ручная сверка.')
    skipped+=1;continue
   order=create_order(db,row)
   if row['status']!='confirmed': change_status(db,order['id'],row['status'],'Перенесено из CSV','migration',notify=False)
   count+=1
  if apply: db.commit()
  else: db.rollback()
  return {'applied':apply,'new_orders':count,'unchanged_orders':skipped}
 except Exception: db.rollback();raise
 finally: db.close()

if __name__=='__main__':
 p=argparse.ArgumentParser(description='Без --apply выполняется только проверка CSV.')
 p.add_argument('csv');p.add_argument('--db',required=True);p.add_argument('--apply',action='store_true');p.add_argument('--demo',action='store_true')
 a=p.parse_args()
 try:print(json.dumps(import_csv(a.csv,a.db,a.apply,a.demo),ensure_ascii=False))
 except Problem as exc:raise SystemExit(exc.message)
