import hashlib, hmac, json, time
from urllib.parse import parse_qsl, urlparse

# Proposed labels, to be approved by the store before live rollout.
STAGES = [
 ('confirmed', 'Заказ подтверждён', 'Мы приняли заказ и готовим его к закупке.'),
 ('purchased', 'Товар закуплен', 'Товар закуплен и готовится к отправке.'),
 ('in_transit', 'В пути в Россию', 'Партия с вашим товаром находится в пути.'),
 ('customs', 'На таможне', 'Партия проходит таможенное оформление.'),
 ('warehouse', 'На складе', 'Товар поступил на склад. Готовим дальнейшую доставку.'),
 ('last_mile', 'Передан в доставку', 'Заказ передан службе доставки до получателя.'),
 ('delivered', 'Заказ получен', 'Доставка завершена.'),
]
STAGE_MAP = {s[0]: {'code':s[0], 'title':s[1], 'description':s[2]} for s in STAGES}

class Problem(Exception):
 def __init__(self, status, message): self.status, self.message = status, message

def validate_init_data(raw, token, now=None):
 if not token or not raw: raise Problem(401, 'Откройте кабинет через Telegram.')
 pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
 if len({k for k,v in pairs}) != len(pairs): raise Problem(401,'Некорректные данные входа.')
 fields = dict(pairs)
 received = fields.pop('hash', '')
 check = '\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
 secret = hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
 expected = hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
 if not hmac.compare_digest(expected, received): raise Problem(401,'Подпись Telegram не подтверждена.')
 try:
  age = (now or time.time()) - int(fields['auth_date'])
  user = json.loads(fields['user'])
  if age > 3600 or age < -30 or type(user.get('id')) is not int or not 0<user['id']<=9223372036854775807: raise ValueError()
 except (KeyError,ValueError,TypeError): raise Problem(401,'Данные входа истекли. Откройте кабинет заново.')
 return user

def https_url(value, hosts=None):
 try:
  u = urlparse(value)
  if u.scheme != 'https' or not u.hostname or u.username or u.password or u.port not in (None,443): raise ValueError()
  if hosts is not None and u.hostname.lower() not in hosts: raise ValueError()
 except ValueError: raise Problem(400,'Нужна HTTPS-ссылка на разрешённый сервис.')
 return value

def required_text(data,key,limit=200):
 v=data.get(key)
 if not isinstance(v,str) or not v.strip() or len(v.strip())>limit: raise Problem(400,f'Проверьте поле {key}.')
 return v.strip()

def positive_int(value,name):
 if isinstance(value,bool): raise Problem(400,f'Проверьте поле {name}.')
 try:
  v=int(value)
  if str(v)!=str(value) or v<=0: raise ValueError()
 except (TypeError,ValueError): raise Problem(400,f'Проверьте поле {name}.')
 return v
