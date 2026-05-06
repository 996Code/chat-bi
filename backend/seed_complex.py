"""Seed chat-bi-test complex business database."""
import asyncio
import os
import random
import sys

import aiomysql
from datetime import date, timedelta

DB_HOST = os.environ.get('SEED_DB_HOST', '')
DB_PORT = int(os.environ.get('SEED_DB_PORT', '3306'))
DB_USER = os.environ.get('SEED_DB_USER', 'root')
DB_PASS = os.environ.get('SEED_DB_PASS', '')
DB_NAME = os.environ.get('SEED_DB_NAME', 'chat-bi-test')
if not DB_HOST or not DB_PASS:
    print("Error: SEED_DB_HOST and SEED_DB_PASS environment variables are required.")
    sys.exit(1)


async def seed():
    conn = await aiomysql.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, db=DB_NAME)
    async with conn.cursor() as cur:
        # 清空旧数据
        await cur.execute('SET FOREIGN_KEY_CHECKS=0')
        for t in ['customer_tickets','expense_claims','invoices','inventory','purchase_orders',
                   'order_items','sales_orders','products','product_categories','customers',
                   'employees','departments','suppliers']:
            await cur.execute(f'TRUNCATE TABLE {t}')
        await cur.execute('SET FOREIGN_KEY_CHECKS=1')

        # ===== 部门 =====
        depts = [
            ('总裁办', None, None, 5000000), ('技术部', None, None, 3000000),
            ('销售部', None, None, 2000000), ('市场部', None, None, 1500000),
            ('人力资源部', None, None, 800000), ('财务部', None, None, 600000),
            ('客服部', None, None, 500000), ('产品部', None, None, 1200000),
        ]
        await cur.executemany('INSERT INTO departments (name, parent_id, manager_id, budget) VALUES (%s, %s, %s, %s)', depts)
        sub_depts = [
            ('后端组', 2, None, 800000), ('前端组', 2, None, 600000), ('数据组', 2, None, 500000),
            ('华北销售', 3, None, 600000), ('华东销售', 3, None, 800000), ('华南销售', 3, None, 600000),
        ]
        await cur.executemany('INSERT INTO departments (name, parent_id, manager_id, budget) VALUES (%s, %s, %s, %s)', sub_depts)

        # ===== 员工 =====
        levels = ['P4','P5','P5','P6','P6','P7','P7','P8','P9']
        salaries = {'P4':8000,'P5':12000,'P6':18000,'P7':25000,'P8':35000,'P9':50000}
        names = ['张伟','李娜','王强','刘洋','陈静','杨帆','赵敏','黄磊','周涛','吴芳',
                 '郑浩','孙丽','马超','朱红','胡明','林峰','何雪','高远','罗琳','谢军',
                 '韩冰','唐亮','曹颖','许达','邓辉','冯蕾','彭勇','蒋梅','蔡鑫','贾玲']
        employees = []
        for i, name in enumerate(names):
            dept_id = (i % 14) + 1
            level = levels[i % len(levels)]
            salary = salaries[level] + random.randint(-2000, 5000)
            hire_date = date(2023, random.randint(1,12), random.randint(1,28))
            status = 'active' if random.random() > 0.1 else 'resigned'
            employees.append((name, dept_id, f'职位{i+1}', level, salary, hire_date,
                             f'138{str(i+1).zfill(8)}', f'emp{i+1}@company.com', status))
        await cur.executemany(
            'INSERT INTO employees (name, department_id, position, level, salary, hire_date, phone, email, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            employees)

        # ===== 客户 =====
        industries = ['互联网','金融','制造','零售','教育','医疗']
        regions = ['华东','华南','华北','华中','西南','西北','东北']
        cities_by_region = {'华东':['上海','杭州','南京','苏州'],'华南':['深圳','广州','厦门'],
                           '华北':['北京','天津'],'华中':['武汉','长沙','郑州'],
                           '西南':['成都','重庆','昆明'],'西北':['西安','兰州'],'东北':['沈阳','大连']}
        levels_c = ['normal','normal','silver','silver','gold','platinum']
        companies = [
            '腾飞科技','华信金控','鼎盛制造','优品零售','学而教育','仁和医疗',
            '云图网络','汇通银行','长虹电器','百汇超市','启明教育','康健医药',
            '星辰互联','国盛证券','永达汽车','家乐福连锁','新东方在线','同仁堂',
            '字节跳动','招商银行','格力电器','永辉超市','好未来','迈瑞医疗',
            '美团','平安保险','美的集团','京东商城','中公教育','爱尔眼科',
            '网易','中信证券','海尔智家','拼多多','传智教育','泰格医药',
        ]
        customers = []
        for i, comp in enumerate(companies):
            region = regions[i % len(regions)]
            city = cities_by_region[region][i % len(cities_by_region[region])]
            level = levels_c[i % len(levels_c)]
            credit = {'normal':100000,'silver':300000,'gold':800000,'platinum':2000000}[level]
            customers.append((comp, f'联系人{i+1}', city, region, industries[i%len(industries)],
                             level, credit, (i%30)+1))
        await cur.executemany(
            'INSERT INTO customers (company_name, contact_name, city, region, industry, level, credit_limit, sales_rep_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
            customers)

        # ===== 产品分类 =====
        cats = [
            ('服务器', None, 0.25), ('网络设备', None, 0.30), ('存储设备', None, 0.28),
            ('安全设备', None, 0.35), ('软件许可', None, 0.60), ('云服务', None, 0.55),
            ('机架服务器', 1, 0.22), ('刀片服务器', 1, 0.20), ('交换机', 2, 0.28),
            ('路由器', 2, 0.32), ('防火墙', 4, 0.38), ('VPN网关', 4, 0.40),
        ]
        await cur.executemany('INSERT INTO product_categories (name, parent_id, margin_rate) VALUES (%s,%s,%s)', cats)

        # ===== 产品 =====
        products = [
            ('SRV-R740','Dell R740 机架服务器',7,45000,32000,'台',25.5,1),
            ('SRV-R640','Dell R640 机架服务器',7,38000,27000,'台',22.0,1),
            ('SRV-BLADE','Dell M640 刀片服务器',8,62000,48000,'片',8.5,1),
            ('SW-9300','Cisco 9300 交换机',9,18000,12000,'台',6.2,1),
            ('SW-9500','Cisco 9500 核心交换机',9,85000,60000,'台',15.0,1),
            ('RT-ISR4K','Cisco ISR 4000 路由器',10,32000,22000,'台',5.8,1),
            ('FW-PA5220','Palo Alto PA-5220 防火墙',11,120000,80000,'台',12.0,1),
            ('VPN-FG60F','Fortinet FG-60F VPN网关',12,15000,9500,'台',3.2,1),
            ('LIC-O365-E3','Office 365 E3 许可',5,348,0,'个/年',0,1),
            ('LIC-WIN-SVR','Windows Server 2022 标准版',5,6500,0,'个',0,1),
            ('CLOUD-AWS-EC2','AWS EC2 m5.xlarge',6,1800,0,'个/月',0,1),
            ('CLOUD-AZURE-VM','Azure D4s v3',6,2100,0,'个/月',0,1),
            ('STG-UNITY','Dell Unity XT 480F 存储',3,180000,130000,'台',45.0,1),
            ('STG-NIMBLE','HPE Nimble 存储',3,95000,68000,'台',30.0,1),
        ]
        await cur.executemany(
            'INSERT INTO products (sku, name, category_id, unit_price, cost_price, unit, weight_kg, is_active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
            products)

        # ===== 供应商 =====
        suppliers = [
            ('Dell 中国', '上海', '021-88881000', 5, 7, 1),
            ('Cisco 中国', '北京', '010-88882000', 5, 14, 1),
            ('Palo Alto 中国', '深圳', '0755-88883000', 4, 21, 1),
            ('Fortinet 中国', '广州', '020-88884000', 4, 10, 1),
            ('微软中国', '上海', '021-88885000', 5, 1, 1),
            ('AWS 中国', '北京', '010-88886000', 5, 0, 1),
            ('Azure 中国', '上海', '021-88887000', 4, 0, 1),
            ('HPE 中国', '北京', '010-88888000', 4, 14, 1),
        ]
        await cur.executemany(
            'INSERT INTO suppliers (name, city, contact_phone, rating, lead_time_days, is_active) VALUES (%s,%s,%s,%s,%s,%s)',
            suppliers)

        # ===== 销售订单 =====
        payment_methods = ['bank_transfer','alipay','wechat','credit']
        pay_statuses = ['pending','paid','partial','refunded']
        deliv_statuses = ['pending','packed','shipped','delivered','returned']
        order_cities = ['北京','上海','深圳','广州','杭州','南京','成都','武汉']

        # 预加载产品价格
        await cur.execute('SELECT id, unit_price, cost_price FROM products')
        prod_prices = {r[0]: (float(r[1]), float(r[2])) for r in await cur.fetchall()}

        for i in range(80):
            cust_id = (i % 36) + 1
            rep_id = (i % 30) + 1
            order_date = date(2025, random.randint(1,12), random.randint(1,28))
            delivery_date = order_date + timedelta(days=random.randint(3,30))

            n_items = random.randint(1,4)
            total = 0.0
            for _ in range(n_items):
                prod_id = random.randint(1,14)
                qty = random.randint(1,10)
                up = prod_prices.get(prod_id, (1000, 500))[0]
                disc = random.choice([0, 0, 0, 0.05, 0.1, 0.15])
                total += round(up * qty * (1-disc), 2)

            discount = round(total * random.choice([0,0,0,0.02,0.05]), 2)
            tax = round((total - discount) * 0.13, 2)
            net = round(total - discount + tax, 2)
            pay_method = random.choice(payment_methods)
            pay_status = random.choice(pay_statuses)
            deliv_status = random.choice(deliv_statuses)
            city = random.choice(order_cities)

            order_no = f'SO-2025-{str(i+1).zfill(4)}'
            await cur.execute(
                'INSERT INTO sales_orders (order_no, customer_id, sales_rep_id, order_date, delivery_date, total_amount, discount_amount, tax_amount, net_amount, payment_method, payment_status, delivery_status, region) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (order_no, cust_id, rep_id, order_date, delivery_date, total, discount, tax, net, pay_method, pay_status, deliv_status, city))

            # 订单明细
            order_id = i + 1
            for _ in range(random.randint(1,3)):
                prod_id = random.randint(1,14)
                qty = random.randint(1,5)
                up = prod_prices.get(prod_id, (1000, 500))[0]
                disc = random.choice([0,0,0,0.05,0.1])
                line_amt = round(up * qty * (1-disc), 2)
                await cur.execute(
                    'INSERT INTO order_items (order_id, product_id, quantity, unit_price, discount_rate, line_amount) VALUES (%s,%s,%s,%s,%s,%s)',
                    (order_id, prod_id, qty, up, disc, line_amt))

        # ===== 库存 =====
        warehouses = ['北京仓','上海仓','深圳仓']
        for prod_id in range(1,15):
            for wh in warehouses:
                qty = random.randint(10, 500)
                safety = random.randint(20, 100)
                await cur.execute(
                    'INSERT INTO inventory (product_id, warehouse, quantity, safety_stock) VALUES (%s,%s,%s,%s)',
                    (prod_id, wh, qty, safety))

        # ===== 采购订单 =====
        po_statuses = ['pending','confirmed','received','cancelled']
        for i in range(30):
            sup_id = random.randint(1,8)
            prod_id = random.randint(1,14)
            qty = random.randint(10,100)
            cost = prod_prices.get(prod_id, (1000, 500))[1]
            if cost == 0: cost = 500
            total_cost = round(cost * qty, 2)
            order_date = date(2025, random.randint(1,12), random.randint(1,28))
            exp_date = order_date + timedelta(days=random.randint(7,30))
            status = random.choice(po_statuses)
            recv_date = exp_date + timedelta(days=random.randint(-3,5)) if status == 'received' else None
            po_no = f'PO-2025-{str(i+1).zfill(4)}'
            await cur.execute(
                'INSERT INTO purchase_orders (po_no, supplier_id, product_id, quantity, unit_cost, total_cost, order_date, expected_date, received_date, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (po_no, sup_id, prod_id, qty, cost, total_cost, order_date, exp_date, recv_date, status))

        # ===== 发票 =====
        inv_statuses = ['issued','paid','overdue','cancelled']
        for i in range(50):
            order_id = random.randint(1, 80)
            await cur.execute('SELECT net_amount FROM sales_orders WHERE id=%s', (order_id,))
            o = await cur.fetchone()
            if not o: continue
            amount = float(o[0])
            issue_date = date(2025, random.randint(1,12), random.randint(1,28))
            due_date = issue_date + timedelta(days=30)
            status = random.choice(inv_statuses)
            paid_date = due_date - timedelta(days=random.randint(0,20)) if status == 'paid' else None
            inv_no = f'INV-2025-{str(i+1).zfill(4)}'
            await cur.execute(
                'INSERT INTO invoices (invoice_no, order_id, amount, tax_rate, issue_date, due_date, paid_date, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                (inv_no, order_id, amount, 0.13, issue_date, due_date, paid_date, status))

        # ===== 报销 =====
        exp_categories = ['travel','meal','office','transport','other']
        exp_statuses = ['pending','approved','rejected']
        for i in range(40):
            emp_id = random.randint(1,30)
            cat = random.choice(exp_categories)
            amount = round(random.uniform(50, 5000), 2)
            claim_date = date(2025, random.randint(1,12), random.randint(1,28))
            status = random.choice(exp_statuses)
            approve_date = claim_date + timedelta(days=random.randint(1,7)) if status != 'pending' else None
            approver_id = random.randint(1,30) if status != 'pending' else None
            await cur.execute(
                'INSERT INTO expense_claims (employee_id, category, amount, claim_date, approve_date, approver_id, status, description) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                (emp_id, cat, amount, claim_date, approve_date, approver_id, status, f'{cat}费用报销'))

        # ===== 客服工单 =====
        ticket_cats = ['complaint','return','consult','bug']
        ticket_priorities = ['low','medium','high','urgent']
        ticket_statuses = ['open','in_progress','resolved','closed']
        for i in range(50):
            cust_id = random.randint(1,36)
            order_id = random.randint(1, 80) if random.random() > 0.3 else None
            cat = random.choice(ticket_cats)
            priority = random.choice(ticket_priorities)
            assignee = random.randint(1,30)
            create_date = date(2025, random.randint(1,12), random.randint(1,28))
            create_dt = f'{create_date} {random.randint(8,18)}:{random.randint(10,59)}:00'
            status = random.choice(ticket_statuses)
            resolve_date = None
            satisfaction = None
            if status in ('resolved','closed'):
                resolve_date = f'{create_date + timedelta(days=random.randint(0,7))} {random.randint(8,18)}:{random.randint(10,59)}:00'
                satisfaction = random.randint(1,5)
            ticket_no = f'TK-2025-{str(i+1).zfill(4)}'
            await cur.execute(
                'INSERT INTO customer_tickets (ticket_no, customer_id, order_id, category, priority, assignee_id, create_date, resolve_date, status, satisfaction_score) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (ticket_no, cust_id, order_id, cat, priority, assignee, create_dt, resolve_date, status, satisfaction))

    await conn.commit()

    # 验证
    async with conn.cursor() as cur:
        for t in ['departments','employees','customers','product_categories','products',
                   'sales_orders','order_items','suppliers','purchase_orders','inventory',
                   'invoices','expense_claims','customer_tickets']:
            await cur.execute(f'SELECT COUNT(*) FROM {t}')
            cnt = await cur.fetchone()
            print(f'  {t}: {cnt[0]} 行')

    conn.close()
    print(f'✅ {DB_NAME} 复杂业务数据库初始化完成')


if __name__ == '__main__':
    asyncio.run(seed())
