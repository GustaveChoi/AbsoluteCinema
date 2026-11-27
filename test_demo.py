"""Integration checks with a disposable database; never touch cinema.db."""
import unittest,tempfile,threading,json,time,urllib.request,urllib.error
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
import app

class DemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='cinema_test_');cls.original=app.DB;app.DB=Path(cls.tmp.name)/'test.db';app.init()
        cls.server=app.CinemaServer(('127.0.0.1',0),'test-admin','test-password');cls.url='http://127.0.0.1:'+str(cls.server.server_port)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();app.DB=cls.original;cls.tmp.cleanup()
    def request(self,path,data=None,cookie=None):
        headers = {'Content-Type':'application/json'}
        if cookie:
            headers['Cookie'] = cookie
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            response = e
        with response:
            body = response.read().decode()
            if response.headers.get_content_type() == 'application/json':
                body = json.loads(body)
            return response.status, body, response.headers

    def req(self,path,data=None,cookie=None):
        return self.request(path,data,cookie)[:2]

    def login(self):
        code, _, headers = self.request('/api/admin/login', {'username':'test-admin','password':'test-password'})
        self.assertEqual(code, 200)
        cookie = headers['Set-Cookie']
        for flag in ('HttpOnly', 'SameSite=Strict', 'Max-Age=28800'):
            self.assertIn(flag, cookie)
        return cookie.split(';')[0]
    def test_01_booking_lifecycle(self):
        code,r=self.req('/api/book',{'user':1,'screening':1,'seat':1});self.assertEqual(code,200);oid=r['id']
        self.assertEqual(self.req('/api/book',{'user':2,'screening':1,'seat':1})[0],409)
        self.assertEqual(self.req('/api/order',{'user':2,'id':oid,'status':'cancelled'})[0],400)
        self.assertEqual(self.req('/api/order',{'user':1,'id':oid,'status':'cancelled'})[0],200)
        code,r=self.req('/api/book',{'user':2,'screening':1,'seat':1});self.assertEqual(code,200)
        self.assertEqual(self.req('/api/order',{'user':2,'id':r['id'],'status':'paid'})[0],200)
        self.assertEqual(self.req('/api/order',{'user':2,'id':r['id'],'status':'cancelled'})[0],400)
        self.assertEqual(self.req('/api/book',{'user':1,'screening':2,'seat':1})[0],200)
        self.assertEqual(self.req('/api/book',{'user':999,'screening':1,'seat':2})[0],409)
        self.assertEqual(self.req('/api/book',{'user':1,'screening':1,'seat':999})[0],409)
        code,seats=self.req('/api/seats?screening=1');self.assertEqual(len(seats),60);self.assertIsNotNone(seats[0]['order_id'])
    def test_02_movie_crud(self):
        cookie = self.login()
        d={'title':'test','duration':100,'genre':'test','description':'test'}
        self.assertEqual(self.req('/api/movie',d,cookie)[0],200)
        movies=self.req('/api/data')[1]['movies'];mid=next(x['id'] for x in movies if x['title']=='test')
        d.update(id=mid,title='changed');self.assertEqual(self.req('/api/movie',d,cookie)[0],200)
        self.assertEqual(next(x['title'] for x in self.req('/api/data')[1]['movies'] if x['id']==mid),'changed')
        self.assertEqual(self.req('/api/movie',{'id':mid,'action':'delete'},cookie)[0],200)
        self.assertEqual(self.req('/api/movie',{'id':1,'action':'delete'},cookie)[0],409)
        self.assertEqual(self.req('/api/movie',{**d,'duration':0},cookie)[0],400)
    def test_03_hall_foreign_key(self):
        with app.connect() as c:
            c.execute("INSERT INTO halls VALUES (2,'Second')")
            c.execute('INSERT INTO seats VALUES (61,2,1,1)')
        self.assertEqual(self.req('/api/book',{'user':1,'screening':1,'seat':61})[0],409)
    def test_04_screening(self):
        cookie = self.login()
        d={'movie_id':1,'hall_id':1,'starts_at':'2026-11-21T12:00','price':1000}
        self.assertEqual(self.req('/api/screening',d,cookie)[0],200)
        self.assertEqual(self.req('/api/screening',d,cookie)[0],409)
        self.assertEqual(self.req('/api/screening',{**d,'starts_at':'bad'},cookie)[0],400)
        self.assertEqual(self.req('/api/hall',{'id':1,'name':'changed'},cookie)[0],200)
        screening = next(s for s in self.req('/api/data')[1]['screenings'] if s['starts_at']=='2026-11-21 12:00')
        code, order = self.req('/api/book', {'user':3,'screening':screening['id'],'seat':1})
        self.assertEqual(code, 200)
        self.assertIn(order['id'], [o['id'] for o in self.req('/api/orders',cookie=cookie)[1]])
        self.assertIn(order['id'], [o['id'] for o in self.req('/api/orders?user=3')[1]])

    def test_05_pages_and_authorization(self):
        _, home = self.req('/')
        self.assertIn('Campus Cinema', home)
        for marker in ('/admin','影院管理','性能实验','全部订单','/api/results'):
            self.assertNotIn(marker, home)
        self.assertIn('管理员登录', self.req('/admin')[1])
        for path in ('/api/movie','/api/screening','/api/hall','/api/experiment','/api/admin/order','/api/admin/logout'):
            with self.subTest(path=path):
                self.assertEqual(self.req(path,{})[0],401)
        for path in ('/api/results','/api/orders','/api/orders?all=1'):
            self.assertEqual(self.req(path)[0],401)
        self.assertEqual(self.req('/api/orders?user=')[0],400)
        for path in ('/admin.html','/results.json','/cinema.db','/性能对比.csv'):
            self.assertEqual(self.req(urllib.parse.quote(path))[0],404)
        self.assertEqual(self.req('/api/admin/login',{'username':'test-admin','password':'wrong'})[0],401)
        cookie = self.login()
        self.assertIn('退出登录',self.req('/admin',cookie=cookie)[1])
        self.assertEqual(self.req('/api/results',cookie=cookie)[0],200)
        code, _, headers = self.request('/api/admin/logout',{},cookie)
        self.assertEqual(code,200)
        self.assertIn('Max-Age=0',headers['Set-Cookie'])
        self.assertIn('管理员登录',self.req('/admin',cookie=cookie)[1])
        self.assertEqual(self.req('/api/orders',cookie=cookie)[0],401)
        with patch.object(app, 'SESSION_SECONDS', 0):
            code, _, headers = self.request('/api/admin/login',{'username':'test-admin','password':'test-password'})
        self.assertEqual(code,200)
        expired = headers['Set-Cookie'].split(';')[0]
        self.assertEqual(self.req('/api/results',cookie=expired)[0],401)
    def test_06_concurrency(self):
        from experiments import race
        r=race(20);self.assertEqual((r['success'],r['conflict'],r['errors'],r['active_orders']),(1,19,0,1))

    def test_07_seat_expiration(self):
        for seat in (10, 11, 12, 13):
            self.assertEqual(self.req('/api/book',{'user':4,'screening':1,'seat':seat})[0],200)
        busy = {o['seat_id']:o['id'] for o in self.req('/api/orders?user=4')[1]}
        self.assertEqual(self.req('/api/order',{'id':busy[13],'user':4,'status':'paid'})[0],200)
        with app.connect() as c:
            c.execute("UPDATE orders SET created_at=datetime('now','-15 minutes') WHERE user_id=4")
        self.assertEqual(self.req('/api/order',{'id':busy[10],'user':4,'status':'paid'})[0],400)
        self.assertEqual(self.req('/api/book',{'user':5,'screening':1,'seat':10})[0],200)
        seats = {seat['id']:seat['order_id'] for seat in self.req('/api/seats?screening=1')[1]}
        self.assertIsNone(seats[11])
        self.assertEqual(seats[13],busy[13])
        with app.connect() as c:
            c.execute("UPDATE orders SET status='pending' WHERE id=?",(busy[12],))
        stop = threading.Event()
        worker = threading.Thread(target=app.sweep_expired,args=(stop,))
        worker.start()
        try:
            deadline = time.monotonic()+8
            while time.monotonic()<deadline:
                with app.connect() as c:
                    status = c.execute('SELECT status FROM orders WHERE id=?',(busy[12],)).fetchone()[0]
                if status=='cancelled':
                    break
                time.sleep(.1)
            self.assertEqual(status,'cancelled')
        finally:
            stop.set()
            worker.join()

    def test_08_admin_experiment(self):
        import experiments
        cookie = self.login()
        with tempfile.TemporaryDirectory() as directory, patch.object(experiments,'ROOT',Path(directory)):
            code, result = self.req('/api/experiment',{},cookie)
            self.assertEqual(code,200)
            self.assertEqual(len(result['queries']),18)
            self.assertEqual(result['races'][-1]['success'],1)
            self.assertEqual(result['races'][-1]['errors'],0)
            self.assertTrue((Path(directory)/'性能对比.csv').is_file())

class LanStartupTest(unittest.TestCase):
    def test_lan_booking(self):
        hostname, addresses = app.lan_hosts()
        if not addresses:
            self.skipTest('没有可测试的非回环 IPv4 地址')
        with tempfile.TemporaryDirectory() as directory:
            for name in ('app.py', 'schema.sql', 'index.html', 'cinema.css', 'demo-password.txt'):
                shutil.copy(app.ROOT/name, directory)
            command = [sys.executable, 'app.py', '--lan']
            if sys.platform == 'darwin':
                launcher = shutil.copy(app.ROOT/'start-lan.command', directory)
                command = [launcher]
            with subprocess.Popen([*command, '--port', '0'],
                                  cwd=directory, stdout=subprocess.PIPE, text=True,
                                  env={**os.environ, 'CINEMA_ADMIN_USER':'', 'CINEMA_ADMIN_PASSWORD':''}) as process:
                try:
                    local = process.stdout.readline().strip()
                    self.assertTrue(local.startswith('Student: http://127.0.0.1:'))
                    port = urllib.parse.urlsplit(local.split(' ', 1)[1]).port
                    base = f'http://{addresses[0]}:{port}'
                    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

                    def request(path, data=None):
                        body = None if data is None else json.dumps(data).encode()
                        req = urllib.request.Request(base+path, body, headers={
                            'Origin':base, 'Content-Type':'application/json'})
                        with client.open(req, timeout=5) as response:
                            return response.read()

                    self.assertIn('Campus Cinema', request('/').decode())
                    self.assertTrue(request('/cinema.css'))
                    login = request('/api/admin/login', {'username':'admin', 'password':'123456'})
                    self.assertEqual(json.loads(login)['message'], '登录成功')
                    order = json.loads(request('/api/book', {'user':1,'screening':1,'seat':1}))
                    request('/api/order', {'user':1,'id':order['id'],'status':'paid'})
                    self.assertEqual(json.loads(request('/api/orders?user=1'))[0]['status'],'paid')
                    print(f'LAN HTTP booking verified: {base}', flush=True)
                    try:
                        resolved = app.socket.gethostbyname_ex(hostname)[2]
                    except OSError:
                        resolved = []
                    if set(resolved).intersection(addresses):
                        with client.open(f'http://{hostname}:{port}/', timeout=5) as response:
                            self.assertIn(b'Campus Cinema', response.read())
                        print(f'LAN hostname verified: {hostname}', flush=True)
                finally:
                    process.terminate()


if __name__=='__main__':unittest.main(verbosity=2)
