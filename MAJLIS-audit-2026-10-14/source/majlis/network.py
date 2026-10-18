"""Native system certificate trust. TLS hostname and certificate checks stay enabled."""
import ssl
from functools import lru_cache
class NetworkSetupError(RuntimeError):pass

@lru_cache(maxsize=1)
def ssl_context():
 try:import truststore
 except ImportError:raise NetworkSetupError('Установите зависимости в интерпретатор PyCharm: python -m pip install -r requirements.txt') from None
 context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
 context.minimum_version=ssl.TLSVersion.TLSv1_2
 return context
