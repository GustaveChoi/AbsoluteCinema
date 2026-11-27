"""Campus Cinema: Python standard library only; local and LAN teaching demo."""
import json, sqlite3, threading, argparse, os, secrets, time, logging
import hashlib
import socket
import subprocess
import sys
from ipaddress import IPv4Address
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT=Path(__file__).resolve().parent
DB=ROOT/'cinema.db'
experiment_lock=threading.Lock()
ADMIN_WRITES = {'/api/movie', '/api/screening', '/api/hall', '/api/experiment',
                '/api/admin/order', '/api/admin/logout'}
SESSION_COOKIE = 'cinema_admin'
STUDENT_COOKIE = 'cinema_student'
PASSWORD_ITERATIONS = 600_000
SESSION_SECONDS = 8 * 60 * 60
EXPIRED = "status='pending' AND created_at <= datetime('now', '-15 minutes')"


class CinemaServer(ThreadingHTTPServer):
    def __init__(self, address, username, password):
        self.admin_username = username.encode()
        self.admin_password = password.encode()
        self.sessions = {}
        self.session_lock = threading.Lock()
        super().__init__(address, Handler)

    def create_session(self, user_id=None):
        with self.session_lock:
            now = time.monotonic()
            self.sessions = {token: session for token, session in self.sessions.items()
                             if session[0] > now}
            token = secrets.token_urlsafe(32)
            self.sessions[token] = (now + SESSION_SECONDS, user_id)
            return token

    def session(self, token):
        with self.session_lock:
            session = self.sessions.get(token)
            if session is not None and session[0] > time.monotonic():
                return session
            self.sessions.pop(token, None)
            return None

    def is_admin(self, token):
        session = self.session(token)
        return session is not None and session[1] is None

    def student_id(self, token):
        session = self.session(token)
        return session[1] if session else None

    def end_session(self, token, student=False):
        with self.session_lock:
            session = self.sessions.get(token)
            if session is not None and (session[1] is not None) == student:
                self.sessions.pop(token)


class Connection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

def connect(path=None):
    c=sqlite3.connect(path or DB,timeout=15,factory=Connection)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def init(path=None):
    with connect(path) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('BEGIN IMMEDIATE')
        users = {row['name'] for row in c.execute('PRAGMA table_info(users)')}
        if users and 'username' not in users:
            c.execute('ALTER TABLE users ADD COLUMN username TEXT')
            c.execute('ALTER TABLE users ADD COLUMN password_salt BLOB')
            c.execute('ALTER TABLE users ADD COLUMN password_hash BLOB')
        columns = {row['name'] for row in c.execute('PRAGMA table_info(orders)')}
        migrate = bool(columns) and 'booking_id' not in columns
        if migrate:
            c.execute('ALTER TABLE orders RENAME TO orders_legacy')
            c.execute('DROP INDEX IF EXISTS uq_active_seat')
            c.execute('DROP INDEX IF EXISTS idx_orders_user')
        # Apply the schema and copy legacy data in the same transaction.
        schema = (ROOT/'schema.sql').read_text(encoding='utf-8')
        statement = ''
        for line in schema.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                c.execute(statement)
                statement = ''
        if migrate:
            c.execute('''INSERT INTO orders(id,user_id,screening_id,hall_id,seat_id,status,amount,created_at)
                         SELECT id,user_id,screening_id,hall_id,seat_id,status,amount,created_at
                         FROM orders_legacy''')
            c.execute('DROP TABLE orders_legacy')
        seed(c)


def seed(c):
    if not c.execute('SELECT 1 FROM users').fetchone():
        c.executemany('INSERT INTO users(id,name) VALUES (?,?)',[(i,f'同学 {i:02d}') for i in range(1,51)])
        c.executemany('INSERT INTO movies VALUES (?,?,?,?,?)',[(1,'星际来信',128,'科幻','跨越光年的一封信，让两位青年重新理解时间与相遇。'),(2,'夏日慢镜头',106,'青春','最后一个校园夏天，一场关于友情与告别的放映。'),(3,'山海之间',115,'纪录','从山谷到海岸，记录普通人与自然的日常。')])
        c.execute("INSERT INTO halls VALUES (1,'光影厅')")
        c.executemany('INSERT INTO seats VALUES (?,1,?,?)',[(r*10+k+1,r+1,k+1) for r in range(6) for k in range(10)])
        c.executemany('INSERT INTO screenings VALUES (?,?,1,?,?)',[(1,1,'2026-11-19 18:30',2500),(2,2,'2026-11-19 21:00',1800),(3,3,'2026-11-20 18:30',2000)])

def rows(c,sql,args=()): return [dict(r) for r in c.execute(sql,args).fetchall()]

def release_expired(c):
    if c.execute('SELECT 1 FROM orders WHERE ' + EXPIRED + ' LIMIT 1').fetchone():
        c.execute("UPDATE orders SET status='cancelled' WHERE " + EXPIRED)


def sweep_expired(stop):
    while not stop.wait(5):
        try:
            with connect() as c:
                release_expired(c)
        except sqlite3.Error:
            logging.exception('释放过期待支付座位失败')


def book(user, screening, seats, path=None):
    if type(seats) is int:
        seats = [seats]
    if (not isinstance(seats, list) or not seats
            or any(type(seat) is not int or not 0 < seat < 2**63 for seat in seats)
            or len(set(seats)) != len(seats)):
        raise ValueError('请选择至少一个有效座位，座位不能重复')
    with connect(path) as c:
        c.execute('BEGIN IMMEDIATE')
        release_expired(c)
        show = c.execute('SELECT hall_id,price FROM screenings WHERE id=?', (screening,)).fetchone()
        if not show:
            raise ValueError('场次不存在')
        created_at = c.execute('SELECT CURRENT_TIMESTAMP').fetchone()[0]
        order_ids = []
        for seat in seats:
            cursor = c.execute('''INSERT INTO orders(user_id,screening_id,hall_id,seat_id,amount,created_at,booking_id)
                                  VALUES (?,?,?,?,?,?,?)''',
                               (user, screening, show['hall_id'], seat, show['price'],
                                created_at, order_ids[0] if order_ids else None))
            order_ids.append(cursor.lastrowid)
        if len(order_ids) > 1:
            c.execute('UPDATE orders SET booking_id=id WHERE id=?', (order_ids[0],))
        return {'id': order_ids[0], 'ids': order_ids, 'amount': show['price'] * len(seats),
                'message': '锁座成功，待模拟支付'}


def change_order(c, order_id, user, status):
    if status == 'refunded':
        refunded = c.execute(
            "UPDATE orders SET status='refunded' WHERE id=? AND user_id=? AND status='paid'",
            (order_id, user))
        if refunded.rowcount != 1:
            raise ValueError('仅能退还该用户已支付的票')
        c.execute('INSERT INTO refunds(order_id,amount) SELECT id,amount FROM orders WHERE id=?',
                  (order_id,))
        return
    if status not in ('paid', 'cancelled'):
        raise ValueError('状态不合法')
    updated = c.execute(
        """UPDATE orders SET status=?
           WHERE user_id=? AND status='pending' AND created_at > datetime('now', '-15 minutes')
             AND (id=? OR booking_id=(SELECT booking_id FROM orders WHERE id=?))""",
        (status, user, order_id, order_id))
    if not updated.rowcount:
        raise ValueError('仅能修改该用户的待支付订单')

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PASSWORD_ITERATIONS)


def student_credentials(data, register):
    username = data.get('username')
    password = data.get('password')
    if not isinstance(username, str) or not isinstance(password, str):
        raise ValueError('请输入用户名和密码')
    username = username.strip()
    if not 1 <= len(username) <= 32 or not 1 <= len(password) <= 128:
        raise ValueError('用户名须为 1–32 字符，密码须为 1–128 字符')
    # Encode before validation so malformed Unicode is rejected for both fields.
    username.encode('utf-8')
    password.encode('utf-8')
    if register:
        salt = secrets.token_bytes(16)
        digest = password_hash(password, salt)
        with connect() as c:
            cur = c.execute(
                'INSERT INTO users(name,username,password_salt,password_hash) VALUES(?,?,?,?) '
                'ON CONFLICT(username) DO NOTHING', (username, username, salt, digest))
            if not cur.rowcount:
                return None
            return cur.lastrowid
    with connect() as c:
        user = c.execute(
            'SELECT id,password_salt,password_hash FROM users WHERE username=?', (username,)).fetchone()
    digest = password_hash(password, user['password_salt'] if user else bytes(16))
    if user and secrets.compare_digest(digest, user['password_hash']):
        return user['id']
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send_body(self, raw, content_type, status=200, cookie=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        if cookie is not None:
            name, value, max_age = cookie
            self.send_header('Set-Cookie',
                             f'{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict')
        self.end_headers()
        self.wfile.write(raw)

    def reply(self,data,status=200,cookie=None):
        raw=json.dumps(data,ensure_ascii=False).encode()
        self.send_body(raw, 'application/json; charset=utf-8', status, cookie)

    def session_token(self, name=SESSION_COOKIE):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', '')).get(name)
        except CookieError:
            return None
        return cookie.value if cookie else None

    def require_admin(self):
        if self.server.is_admin(self.session_token()):
            return True
        self.reply({'error':'请先登录管理员账号'},401)
        return False

    def do_GET(self):
        p=urlparse(self.path);q=parse_qs(p.query, keep_blank_values=True)
        user = self.server.student_id(self.session_token(STUDENT_COOKIE))
        if p.path in ('/', '/admin', '/cinema.css'):
            file, kind = ('index.html' if user else 'student_login.html'), 'text/html'
            if p.path == '/admin':
                file = 'admin.html' if self.server.is_admin(self.session_token()) else 'admin_login.html'
            elif p.path == '/cinema.css':
                file, kind = 'cinema.css', 'text/css'
            self.send_body((ROOT/file).read_bytes(), kind+'; charset=utf-8')
            return
        if p.path in ('/api/results', '/api/admin/orders') and not self.require_admin():
            return
        if not user and (p.path in ('/api/seats', '/api/orders') or
                         (p.path == '/api/data' and not self.server.is_admin(self.session_token()))):
            self.reply({'error':'请先登录学生账号'},401)
            return
        try:
            with connect() as c:
                if p.path=='/api/data':
                    data = {t:rows(c,'SELECT * FROM '+t) for t in ['movies','halls','screenings']}
                    if user:
                        data['username'] = c.execute('SELECT name FROM users WHERE id=?', (user,)).fetchone()[0]
                    self.reply(data)
                elif p.path=='/api/seats':
                    release_expired(c)
                    c.commit()
                    self.reply(rows(c,"SELECT s.*,o.id AS order_id FROM seats s JOIN screenings sc ON sc.hall_id=s.hall_id LEFT JOIN orders o ON o.seat_id=s.id AND o.screening_id=sc.id AND o.status IN ('pending','paid') WHERE sc.id=? ORDER BY row_no,col_no",(int(q['screening'][0]),)))
                elif p.path in ('/api/orders', '/api/admin/orders'):
                    release_expired(c)
                    c.commit()
                    sql='''SELECT o.*,u.name,m.title,s.starts_at,h.name hall,st.row_no,st.col_no,
                                  r.id AS refund_id,r.amount AS refund_amount,r.created_at AS refunded_at
                           FROM orders o JOIN users u ON u.id=o.user_id
                           JOIN screenings s ON s.id=o.screening_id JOIN movies m ON m.id=s.movie_id
                           JOIN halls h ON h.id=o.hall_id JOIN seats st ON st.id=o.seat_id
                           LEFT JOIN refunds r ON r.order_id=o.id'''
                    args=()
                    if p.path == '/api/orders': sql+=' WHERE o.user_id=?';args=(user,)
                    self.reply(rows(c,sql+' ORDER BY o.id DESC',args))
                elif p.path=='/api/results':
                    f=ROOT/'results.json';self.reply(json.loads(f.read_text(encoding='utf-8')) if f.exists() else {})
                else:self.reply({'error':'路径不存在'},404)
        except (ValueError,KeyError,sqlite3.Error) as e:self.reply({'error':str(e)},400)
    def do_POST(self):
        try:
            p=urlparse(self.path).path
            if p in ADMIN_WRITES and not self.require_admin():
                return
            if self.headers.get('Origin') and self.headers['Origin'] != 'http://'+self.headers.get('Host',''): raise ValueError('仅允许同源请求')
            n=int(self.headers.get('Content-Length',0))
            if not 0 <= n <= 10000: raise ValueError('请求大小不合法')
            d=json.loads(self.rfile.read(n)) if n else {}
            if not isinstance(d, dict): raise ValueError('请求必须是 JSON 对象')
            if p == '/api/admin/login':
                username = str(d.get('username', '')).encode()
                password = str(d.get('password', '')).encode()
                if not (secrets.compare_digest(username, self.server.admin_username)
                        and secrets.compare_digest(password, self.server.admin_password)):
                    self.reply({'error':'管理员账号或密码错误'},401)
                    return
                self.server.end_session(self.session_token())
                token = self.server.create_session()
                self.reply({'message':'登录成功'},cookie=(SESSION_COOKIE, token, SESSION_SECONDS))
                return
            if p == '/api/admin/logout':
                self.server.end_session(self.session_token())
                self.reply({'message':'已退出登录'},cookie=(SESSION_COOKIE, '', 0))
                return
            if p in ('/api/student/register', '/api/student/login'):
                register = p.endswith('/register')
                user = student_credentials(d, register)
                if user is None:
                    self.reply({'error':'用户名已被注册' if register else '用户名或密码错误'},
                               409 if register else 401)
                    return
                previous = self.session_token(STUDENT_COOKIE)
                self.server.end_session(previous, student=True)
                token = self.server.create_session(user)
                self.reply({'message':'注册成功' if register else '登录成功'},
                           201 if register else 200, (STUDENT_COOKIE, token, SESSION_SECONDS))
                return
            if p == '/api/student/logout':
                self.server.end_session(self.session_token(STUDENT_COOKIE), student=True)
                self.reply({'message':'已退出登录'}, cookie=(STUDENT_COOKIE, '', 0))
                return
            user = self.server.student_id(self.session_token(STUDENT_COOKIE))
            if p in ('/api/book', '/api/order') and not user:
                self.reply({'error':'请先登录学生账号'},401)
                return
            if p == '/api/book':
                seats = d['seats'] if 'seats' in d else [int(d['seat'])]
                if not isinstance(seats, list):
                    raise ValueError('seats 必须是座位数组')
                self.reply(book(user, int(d['screening']), seats))
                return
            if p=='/api/experiment':
                if not experiment_lock.acquire(False):self.reply({'error':'实验正在运行，请稍后刷新'},409);return
                try:
                    from experiments import run
                    self.reply(run())
                finally:experiment_lock.release()
                return
            with connect() as c:
                if p in ('/api/order', '/api/admin/order'):
                    change_order(c, int(d['id']), user if p == '/api/order' else int(d['user']), d['status'])
                elif p=='/api/movie':
                    if d.get('action')=='delete':c.execute('DELETE FROM movies WHERE id=?',(int(d['id']),))
                    else:
                        title=str(d['title']).strip();duration=int(d['duration'])
                        if not title or len(title)>80 or not 1<=duration<=600:raise ValueError('请输入有效片名和 1–600 分钟片长')
                        values=(title,duration,str(d['genre']),str(d.get('description','')))
                        if d.get('id'):c.execute('UPDATE movies SET title=?,duration=?,genre=?,description=? WHERE id=?',values+(int(d['id']),))
                        else:c.execute('INSERT INTO movies(title,duration,genre,description) VALUES (?,?,?,?)',values)
                elif p=='/api/hall':
                    name=str(d['name']).strip()
                    if not name:raise ValueError('影厅名称不能为空')
                    c.execute('UPDATE halls SET name=? WHERE id=?',(name,int(d['id'])))
                elif p=='/api/screening':
                    from datetime import datetime
                    starts=datetime.fromisoformat(d['starts_at']).strftime('%Y-%m-%d %H:%M')
                    values=(int(d['movie_id']),int(d['hall_id']),starts,int(d['price']))
                    if d.get('id'):
                        c.execute('BEGIN IMMEDIATE')
                        if c.execute('SELECT 1 FROM orders WHERE screening_id=?',(int(d['id']),)).fetchone():raise ValueError('已有订单的场次不可修改')
                        c.execute('UPDATE screenings SET movie_id=?,hall_id=?,starts_at=?,price=? WHERE id=?',values+(int(d['id']),))
                    else:c.execute('INSERT INTO screenings(movie_id,hall_id,starts_at,price) VALUES (?,?,?,?)',values)
                else:raise ValueError('未知操作')
                c.commit()
            self.reply({'message':'操作成功'})
        except sqlite3.IntegrityError as e:self.reply({'error':'座位已被占用，或操作违反外键/唯一约束。请刷新重试。','detail':str(e)},409)
        except (ValueError,KeyError,TypeError,sqlite3.Error) as e:self.reply({'error':str(e)},400)

def mac_setting(*command):
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL,
                                       timeout=2).strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def lan_hosts():
    hostname = socket.gethostname()
    addresses = []
    if sys.platform == 'darwin':
        label = mac_setting('/usr/sbin/scutil', '--get', 'LocalHostName')
        hostname = label + '.local' if label else hostname
        for interface in ('en0', 'en1'):
            address = mac_setting('/usr/sbin/ipconfig', 'getifaddr', interface)
            if address:
                addresses.append(address)
    try:
        addresses.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    # A UDP route lookup selects a local interface without sending a packet.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        try:
            route.connect(('192.0.2.1', 9))
            addresses.append(route.getsockname()[0])
        except OSError:
            pass
    ips = [ip for ip in dict.fromkeys(addresses)
           if not IPv4Address(ip).is_loopback and not IPv4Address(ip).is_unspecified]
    return hostname, ips


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--lan', action='store_true', help='accept connections on all IPv4 interfaces')
    args = parser.parse_args()
    username = os.getenv('CINEMA_ADMIN_USER') or 'admin'
    password = os.getenv('CINEMA_ADMIN_PASSWORD') or (ROOT/'demo-password.txt').read_text(encoding='utf-8').strip()
    init()
    host = '0.0.0.0' if args.lan else '127.0.0.1'
    with CinemaServer((host,args.port), username, password) as server:
        with connect() as c:
            release_expired(c)
        base = f'http://127.0.0.1:{server.server_port}'
        print(f'Student: {base}/\nAdmin:   {base}/admin\nAdmin username: {username}', flush=True)
        if args.lan:
            hostname, addresses = lan_hosts()
            print(f'LAN name (if resolved by client): http://{hostname}:{server.server_port}/')
            for address in addresses:
                print(f'LAN IP: http://{address}:{server.server_port}/')
            if not addresses:
                print('Check the Wi-Fi IPv4 address in network settings.')
            sys.stdout.flush()
        stop = threading.Event()
        sweeper = threading.Thread(target=sweep_expired, args=(stop,), daemon=True)
        sweeper.start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            sweeper.join()
