from pymongo import MongoClient
from bson.objectid import ObjectId  # 处理MongoDB的_id
import datetime
from config import MONGODB_SETTINGS  # 导入配置

# 1. 连接数据库，获取集合（先执行这步初始化）
client = MongoClient(MONGODB_SETTINGS['host'], MONGODB_SETTINGS['port'])
db = client[MONGODB_SETTINGS['db']]
collection = db['data_set']  # 对应“用户数据单”的集合


# 2. 添加用户数据单（例10-3）
def insert_data_set(user_id, data_list):
    try:
        data_set = {
            'user_id': user_id,
            'date': datetime.datetime.now(),
            'data_list': data_list,
            'complete': 0  # 0表示未完成
        }
        re = collection.insert_one(data_set)
        print(f"添加成功，数据ID：{re.inserted_id}")
        return re
    except Exception as e:
        print(f"添加失败：{e}")
        return None


# 3. 查找某用户的数据单列表（例10-4）
def find_data_set(user_id):
    try:
        # 查询条件：user_id匹配 + complete=0（未完成）
        results = collection.find({'user_id': user_id, 'complete': 0})
        data_list = [res for res in results]  # 转成列表
        print(f"找到{len(data_list)}条数据")
        return data_list
    except Exception as e:
        print(f"查询失败：{e}")
        return None


# 4. 根据数据单ID创建任务（例10-5）
def create_task(id):
    try:
        # 注意：MongoDB的_id是ObjectId类型，需要转换
        result = collection.find_one({'_id': ObjectId(id)})
        if not result:
            print("数据单不存在")
            return False

        # 这里模拟Task.create_task（你可以替换成实际的任务创建逻辑）
        print(f"为用户{result['user_id']}创建任务，数据单ID：{str(result['_id'])}")
        for item in result['data_list']:
            print(f"处理数据项：{item.get('id')}")  # 假设data_list里的元素有id字段
        return True
    except Exception as e:
        print(f"创建任务失败：{e}")
        return False


# 5. 更改数据单状态为已完成（例10-6）
def set_complete(_id):
    try:
        condition = {'_id': ObjectId(_id)}
        # 更简洁的更新方式（原代码的写法可以简化）
        result = collection.update_one(
            condition,
            {'$set': {'complete': 1}}  # 直接修改complete字段为1
        )
        if result.modified_count > 0:
            print("状态更新为已完成")
            return True
        else:
            print("未找到数据单或无需更新")
            return False
    except Exception as e:
        print(f"更新状态失败：{e}")
        return None

if __name__ == "__main__":
    # 测试1：添加数据单
    test_data_list = [{'id': 'data_1', 'content': '测试数据1'}, {'id': 'data_2', 'content': '测试数据2'}]
    insert_result = insert_data_set(user_id='user_001', data_list=test_data_list)
    if insert_result:
        data_id = str(insert_result.inserted_id)  # 获取数据单的ID

        # 测试2：查询该用户的未完成数据单
        find_result = find_data_set(user_id='user_001')
        print("查询结果：", find_result)

        # 测试3：根据ID创建任务
        create_task(id=data_id)

        # 测试4：更新状态为已完成
        set_complete(_id=data_id)

        # 再次查询，确认状态变化
        print("更新后的查询结果：", find_data_set(user_id='user_001'))