from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Numeric

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'user'
    id        = db.Column(db.Integer, primary_key=True)
    username  = db.Column(db.String(80),  unique=True, nullable=False)   # 只允许英文
    password  = db.Column(db.String(512),                 nullable=False)
    role      = db.Column(db.String(20),                  nullable=False) # user / finance / boss
    realname  = db.Column(db.Unicode(40),                 nullable=False) # 中文姓名

class ExpenseType(db.Model):
    __tablename__ = 'expense_type'
    id   = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Unicode(40), unique=True, nullable=False)

class Expense(db.Model):
    __tablename__ = 'expense'
    id          = db.Column(db.Integer, primary_key=True)
    date        = db.Column(db.Date,      nullable=False)
    type_id     = db.Column(db.Integer, db.ForeignKey('expense_type.id'), nullable=False)
    type        = db.relationship('ExpenseType')
    title       = db.Column(db.Unicode(200),  nullable=False)
    amount      = db.Column(Numeric(10, 2),   nullable=False)
    invoice     = db.Column(db.Unicode(200))                # 允许中英文文件名
    description = db.Column(db.UnicodeText)
    status      = db.Column(db.Unicode(20),  default='待审批') # 中文流程状态

    submitter_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    approver_id  = db.Column(db.Integer, db.ForeignKey('user.id'))

    submitter = db.relationship('User', foreign_keys=[submitter_id],
                                backref='expenses_submitted')
    approver  = db.relationship('User', foreign_keys=[approver_id],
                                backref='expenses_approved')
    reject_reason = db.Column(db.String(255))
