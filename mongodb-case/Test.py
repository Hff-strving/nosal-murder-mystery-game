from pymongo import MongoClient

# 直接指定虚拟机IP和端口，禁用SSL（本地测试无需）
client = MongoClient("mongodb://192.168.153.135:27017", connectTimeoutMS=5000)
# 测试连接
try:
    client.admin.command('ping')  # 发送ping命令验证
    print("连接成功！")
    db = client['da']  # 切换到da数据库
    db.test.insert_one({"name": "test"})  # 插入数据测试
except Exception as e:
    print("连接失败：", e)