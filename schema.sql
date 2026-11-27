PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,username TEXT,password_salt BLOB,password_hash BLOB);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username ON users(username);
CREATE TABLE IF NOT EXISTS movies(id INTEGER PRIMARY KEY,title TEXT NOT NULL,duration INTEGER NOT NULL CHECK(duration>0),genre TEXT NOT NULL,description TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS halls(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS seats(id INTEGER PRIMARY KEY,hall_id INTEGER NOT NULL REFERENCES halls(id),row_no INTEGER NOT NULL,col_no INTEGER NOT NULL,UNIQUE(hall_id,row_no,col_no),UNIQUE(id,hall_id));
CREATE TABLE IF NOT EXISTS screenings(id INTEGER PRIMARY KEY,movie_id INTEGER NOT NULL REFERENCES movies(id),hall_id INTEGER NOT NULL REFERENCES halls(id),starts_at TEXT NOT NULL,price INTEGER NOT NULL CHECK(price>=0),UNIQUE(id,hall_id),UNIQUE(hall_id,starts_at));
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),screening_id INTEGER NOT NULL,hall_id INTEGER NOT NULL,seat_id INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','paid','cancelled','refunded')),amount INTEGER NOT NULL CHECK(amount>=0),created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,booking_id INTEGER,FOREIGN KEY(screening_id,hall_id) REFERENCES screenings(id,hall_id),FOREIGN KEY(seat_id,hall_id) REFERENCES seats(id,hall_id));
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_seat ON orders(screening_id,seat_id) WHERE status IN ('pending','paid');
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_booking ON orders(booking_id);
CREATE TABLE IF NOT EXISTS refunds(
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
    amount INTEGER NOT NULL CHECK(amount>=0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
