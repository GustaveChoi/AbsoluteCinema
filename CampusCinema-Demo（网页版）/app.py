"""Campus Cinema: Python standard library only; localhost teaching demo."""
import json, sqlite3, threading, argparse, os, secrets, time, logging
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
SESSION_SECONDS = 8 * 60 * 60
EXPIRED = "status='pending' AND created_at <= datetime('now', '-15 minutes')"


class CinemaServer(ThreadingHTTPServer):
    def __init__(self, address, username, password):
        self.admin_username = username.encode()
        self.admin_password = password.encode()
        self.sessions = {}
        self.session_lock = threading.Lock()
        super().__init__(address, Handler)

    def create_session(self, previous):
        with self.session_lock:
            now = time.monotonic()
            self.sessions = {token: deadline for token, deadline in self.sessions.items()
                             if deadline > now and token != previous}
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + SESSION_SECONDS
            return token

    def is_admin(self, token):
        with self.session_lock:
            deadline = self.sessions.get(token)
            if deadline is not None and deadline > time.monotonic():
                return True
            self.sessions.pop(token, None)
            return False

    def end_session(self, token):
        with self.session_lock:
            self.sessions.pop(token, None)

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
    c=connect(path)
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript((ROOT/'schema.sql').read_text(encoding='utf-8'))
    if not c.execute('SELECT 1 FROM users').fetchone():
        c.executemany('INSERT INTO users VALUES (?,?)',[(i,f'同学 {i:02d}') for i in range(1,51)])
        c.executemany('INSERT INTO movies VALUES (?,?,?,?,?)',[(1,'星际来信',128,'科幻','跨越光年的一封信，让两位青年重新理解时间与相遇。'),(2,'夏日慢镜头',106,'青春','最后一个校园夏天，一场关于友情与告别的放映。'),(3,'山海之间',115,'纪录','从山谷到海岸，记录普通人与自然的日常。')])
        c.execute("INSERT INTO halls VALUES (1,'光影厅')")
        c.executemany('INSERT INTO seats VALUES (?,1,?,?)',[(r*10+k+1,r+1,k+1) for r in range(6) for k in range(10)])
        c.executemany('INSERT INTO screenings VALUES (?,?,1,?,?)',[(1,1,'2026-11-19 18:30',2500),(2,2,'2026-11-19 21:00',1800),(3,3,'2026-11-20 18:30',2000)])
        c.commit()
    c.close()

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


def book(user,screening,seat,path=None):
    c=connect(path)
    try:
        c.execute('BEGIN IMMEDIATE')
        release_expired(c)
        show=c.execute('SELECT hall_id,price FROM screenings WHERE id=?',(screening,)).fetchone()
        if not show: raise ValueError('场次不存在')
        cur=c.execute('INSERT INTO orders(user_id,screening_id,hall_id,seat_id,amount) VALUES (?,?,?,?,?)',(user,screening,show['hall_id'],seat,show['price']))
        c.commit()
        return {'id':cur.lastrowid,'message':'锁座成功，待模拟支付'}
    finally: c.close()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send_body(self, raw, content_type, status=200, cookie=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        if cookie is not None:
            value, max_age = cookie
            self.send_header('Set-Cookie',
                             f'{SESSION_COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict')
        self.end_headers()
        self.wfile.write(raw)

    def reply(self,data,status=200,cookie=None):
        raw=json.dumps(data,ensure_ascii=False).encode()
        self.send_body(raw, 'application/json; charset=utf-8', status, cookie)

    def session_token(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', '')).get(SESSION_COOKIE)
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
        if p.path in ('/', '/admin', '/cinema.css'):
            file, kind = 'index.html', 'text/html'
            if p.path == '/admin':
                file = 'admin.html' if self.server.is_admin(self.session_token()) else 'admin_login.html'
            elif p.path == '/cinema.css':
                file, kind = 'cinema.css', 'text/css'
            self.send_body((ROOT/file).read_bytes(), kind+'; charset=utf-8')
            return
        if (p.path == '/api/results' or (p.path == '/api/orders' and 'user' not in q)) and not self.require_admin():
            return
        try:
            with connect() as c:
                if p.path=='/api/data':
                    self.reply({t:rows(c,'SELECT * FROM '+t) for t in ['users','movies','halls','screenings']})
                elif p.path=='/api/seats':
                    release_expired(c)
                    c.commit()
                    self.reply(rows(c,"SELECT s.*,o.id AS order_id FROM seats s JOIN screenings sc ON sc.hall_id=s.hall_id LEFT JOIN orders o ON o.seat_id=s.id AND o.screening_id=sc.id AND o.status IN ('pending','paid') WHERE sc.id=? ORDER BY row_no,col_no",(int(q['screening'][0]),)))
                elif p.path=='/api/orders':
                    release_expired(c)
                    c.commit()
                    sql='SELECT o.*,u.name,m.title,s.starts_at,h.name hall,st.row_no,st.col_no FROM orders o JOIN users u ON u.id=o.user_id JOIN screenings s ON s.id=o.screening_id JOIN movies m ON m.id=s.movie_id JOIN halls h ON h.id=o.hall_id JOIN seats st ON st.id=o.seat_id'
                    args=()
                    if 'user' in q: sql+=' WHERE o.user_id=?';args=(int(q['user'][0]),)
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
                token = self.server.create_session(self.session_token())
                self.reply({'message':'登录成功'},cookie=(token, SESSION_SECONDS))
                return
            if p == '/api/admin/logout':
                self.server.end_session(self.session_token())
                self.reply({'message':'已退出登录'},cookie=('', 0))
                return
            if p=='/api/book': self.reply(book(int(d['user']),int(d['screening']),int(d['seat'])));return
            if p=='/api/experiment':
                if not experiment_lock.acquire(False):self.reply({'error':'实验正在运行，请稍后刷新'},409);return
                try:
                    from experiments import run
                    self.reply(run())
                finally:experiment_lock.release()
                return
            with connect() as c:
                if p in ('/api/order', '/api/admin/order'):
                    if d['status'] not in ('paid','cancelled'):raise ValueError('状态不合法')
                    cur=c.execute("UPDATE orders SET status=? WHERE id=? AND user_id=? AND status='pending' "
                                  "AND created_at > datetime('now', '-15 minutes')",
                                  (d['status'],int(d['id']),int(d['user'])))
                    if cur.rowcount!=1:raise ValueError('仅能修改该用户的待支付订单')
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

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8766);args=parser.parse_args()
    username = os.getenv('CINEMA_ADMIN_USER') or 'admin'
    configured_password = os.getenv('CINEMA_ADMIN_PASSWORD')
    password = configured_password or secrets.token_urlsafe(15)
    init()
    with CinemaServer(('127.0.0.1',args.port), username, password) as server:
        with connect() as c:
            release_expired(c)
        base = f'http://127.0.0.1:{server.server_port}'
        print(f'Student: {base}/\nAdmin:   {base}/admin\nAdmin username: {username}', flush=True)
        if not configured_password:
            print(f'Temporary admin password: {password}', flush=True)
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
