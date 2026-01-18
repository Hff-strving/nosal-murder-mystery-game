import uuid
from pymongo import MongoClient
from config import MONGODB_SETTINGS

# 初始化MongoDB连接（复用之前的配置）
client = MongoClient(MONGODB_SETTINGS['host'], MONGODB_SETTINGS['port'])
db = client[MONGODB_SETTINGS['db']]
# 这里用之前的文档集合（也可以单独建集合，根据实际需求调整）
doc_collection = db['document_collection']


# 例10-11：向tokenList中添加一个token
def addToken(condition, token):
    """
    :param condition: 查询条件，如: {"documentId": "doc_001"}
    :param token: token内容（字典）
    :return: tokenid字符串
    """
    try:
        # 生成唯一tokenid（去除uuid中的'-'）
        tokenid = str(uuid.uuid1()).replace('-', '')
        # 给token添加tokenid字段
        token.update({'tokenid': tokenid})
        # 使用$addToSet向tokenList数组添加元素（避免重复）
        doc_collection.update_one(
            condition,
            {"$addToSet": {"tokenList": token}}
        )
        print(f"token添加成功，tokenid：{tokenid}")
        return tokenid
    except Exception as e:
        print(f"添加token失败：{e}")
        return None


# 例10-12：从tokenList中删除一个token
def deleteToken(condition, token):
    """
    :param condition: 查询条件，如: {"documentId": "doc_001"}
    :param token: 要删除的token（字典，含tokenid）
    :return: 更新结果对象
    """
    try:
        # 使用$pull从tokenList数组中删除匹配的token
        result = doc_collection.update_one(
            condition,
            {"$pull": {"tokenList": token}}
        )
        if result.modified_count > 0:
            print("token删除成功")
        else:
            print("未找到匹配的token或无需删除")
        return result
    except Exception as e:
        print(f"删除token失败：{e}")
        return None


# 例10-13：在tokenList中查找一个token
def findToken(condition, token):
    """
    :param condition: 查询条件，如: {"documentId": "doc_001"}
    :param token: 要查找的token（字典，含tokenid）
    :return: 匹配的token内容
    """
    try:
        # 先查询对应的文档
        result = doc_collection.find_one(condition)
        if result is None:
            print("未找到匹配的文档")
            return None
        # 获取文档中的tokenList数组
        tokenList = result.get('tokenList', [])
        # 遍历tokenList查找匹配的tokenid
        for t in tokenList:
            if t.get('tokenid') == token.get('tokenid'):
                print("找到匹配的token")
                return t
        print("未找到匹配的token")
        return None
    except Exception as e:
        print(f"查找token失败：{e}")
        return None


# 测试
if __name__ == "__main__":
    # 测试用的文档条件（先确保该文档已存在，比如之前插入的doc_001）
    test_condition = {"documentId": "doc_001"}

    # 测试添加token
    test_token = {"name": "测试实体", "value": "测试值"}
    token_id = addToken(test_condition, test_token)

    # 测试查找token
    found_token = findToken(test_condition, {"tokenid": token_id})
    print("查找的token：", found_token)

    # 测试删除token
    deleteToken(test_condition, {"tokenid": token_id})

    # 再次查找，确认删除
    after_delete = findToken(test_condition, {"tokenid": token_id})
    print("删除后查找结果：", after_delete)