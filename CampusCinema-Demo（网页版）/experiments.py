import sqlite3,time,statistics,json,csv,tempfile,threading,platform,math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime,timezone,timedelta
from app import init,book,connect,ROOT

def timing(c,sql,args=(),repeats=21):
    for _ in range(3):c.execute(sql,args).fetchall()
    samples=[]
    for _ in range(repeats):
        t=time.perf_counter_ns(); result=c.execute(sql,args).fetchall();samples.append((time.perf_counter_ns()-t)/1e6)
    return {'median_ms':round(statistics.median(samples),5),'p95_ms':round(sorted(samples)[math.ceil(.95*len(samples))-1],5),'samples_ms':samples,'returned':len(result)}

def race(workers,safe=True):
    with tempfile.TemporaryDirectory(prefix='cinema_race_') as folder:
        path=Path(folder)/'race.db';init(path)
        if not safe:
            with connect(path) as c:c.execute('DROP INDEX uq_active_seat')
        barrier=threading.Barrier(workers)
        def buyer(user):
            t=time.perf_counter_ns()
            try:
                if safe:
                    barrier.wait(timeout=20);book(user,1,1,path)
                else:
                    # Deterministic interleaving: all buyers read before any writes.
                    c=connect(path)
                    exists=c.execute("SELECT 1 FROM orders WHERE screening_id=1 AND seat_id=1 AND status IN ('pending','paid')").fetchone()
                    barrier.wait(timeout=20)
                    if not exists:
                        c.execute('INSERT INTO orders(user_id,screening_id,hall_id,seat_id,amount) VALUES (?,1,1,1,2500)',(user,));c.commit()
                    c.close()
                status='success'
            except sqlite3.IntegrityError:status='conflict'
            except (sqlite3.OperationalError,threading.BrokenBarrierError):status='error'
            return status,(time.perf_counter_ns()-t)/1e6
        t=time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:results=list(pool.map(buyer,range(1,workers+1)))
        elapsed=(time.perf_counter()-t)*1000
        with connect(path) as c:active=c.execute("SELECT COUNT(*) FROM orders WHERE status IN ('pending','paid')").fetchone()[0]
        return {'workers':workers,'mode':'事务 + 唯一约束' if safe else '错误示范：先查再写，无唯一约束','success':sum(s=='success' for s,_ in results),'conflict':sum(s=='conflict' for s,_ in results),'errors':sum(s=='error' for s,_ in results),'active_orders':active,'elapsed_ms':round(elapsed,3),'p95_ms':round(sorted(t for _,t in results)[math.ceil(.95*workers)-1],3)}

def run():
    output={'generated_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),'engine':'SQLite '+sqlite3.sqlite_version,'python':platform.python_version(),'platform':platform.platform(),'method':'固定均匀合成数据；每100条订单约1条属于用户42；3次预热 + 21次测量；计时包括执行和fetchall，不含建库/造数/建索引/HTTP；主键索引始终保留；无索引指无orders.user_id辅助索引；缓存热态；连接查询返回相同订单集合；PRAGMA automatic_index=OFF；DELETE日志模式，synchronous=FULL。','queries':[],'crud':[],'races':[]}
    with tempfile.TemporaryDirectory(prefix='cinema_bench_') as folder:
        for n in (1000,10000,100000):
            c=sqlite3.connect(Path(folder)/f'bench{n}.db')
            c.executescript('PRAGMA automatic_index=OFF; CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT); CREATE TABLE screenings(id INTEGER PRIMARY KEY,title TEXT); CREATE TABLE orders(id INTEGER PRIMARY KEY,user_id INTEGER,screening_id INTEGER,amount INTEGER);')
            c.executemany('INSERT INTO users VALUES (?,?)',[(i,f'user{i}') for i in range(1,101)])
            c.executemany('INSERT INTO screenings VALUES (?,?)',[(i,f'show{i}') for i in range(1,101)])
            c.executemany('INSERT INTO orders VALUES (?,?,?,?)',((i,i%100+1,(i*7)%100+1,2500) for i in range(1,n+1)));c.commit()
            for indexed in (False,True):
                if indexed:c.execute('CREATE INDEX idx_bench_user ON orders(user_id)');c.commit()
                for tables,joins in [(1,''),(2,' JOIN users u ON u.id=o.user_id'),(3,' JOIN users u ON u.id=o.user_id JOIN screenings s ON s.id=o.screening_id')]:
                    sql='SELECT o.id,o.amount FROM orders o'+joins+' WHERE o.user_id=?'
                    output['queries'].append({'n':n,'indexed':indexed,'tables':tables,'sql':sql,'params':[42],'plan':[r[3] for r in c.execute('EXPLAIN QUERY PLAN '+sql,(42,))],**timing(c,sql,(42,))})
                for label,sql,args in [('INSERT','INSERT INTO orders VALUES (?,?,?,?)',(n+1,42,1,2500)),('UPDATE','UPDATE orders SET amount=2600 WHERE user_id=?',(42,)),('DELETE','DELETE FROM orders WHERE user_id=?',(42,))]:
                    samples=[]
                    for rep in range(24):
                        c.execute('BEGIN');t=time.perf_counter_ns();cur=c.execute(sql,args);ms=(time.perf_counter_ns()-t)/1e6;affected=cur.rowcount;c.rollback()
                        if rep>=3:samples.append(ms)
                    output['crud'].append({'n':n,'indexed':indexed,'operation':label,'affected':affected,'median_ms':round(statistics.median(samples),5),'samples_ms':samples})
            c.close()
    for workers,safe in [(1,True),(20,False),(20,True),(50,True)]:output['races'].append(race(workers,safe))
    # Atomic replacement so a refresh cannot read partially written JSON.
    dest=ROOT/'results.json';tmp=ROOT/'results.tmp';tmp.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(dest)
    with (ROOT/'性能对比.csv').open('w',newline='',encoding='utf-8-sig') as f:
        keys=['n','indexed','tables','median_ms','p95_ms','returned'];w=csv.DictWriter(f,keys,extrasaction='ignore');w.writeheader();w.writerows(output['queries'])
    return output

if __name__=='__main__':
    r=run();print(json.dumps({'queries':[{k:v for k,v in x.items() if k in ('n','indexed','tables','median_ms')} for x in r['queries']],'races':r['races']},ensure_ascii=False,indent=2))
