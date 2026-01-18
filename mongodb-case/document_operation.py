# document_operation.py
from pymongo import MongoClient
from config import MONGODB_SETTINGS

# 初始化MongoDB连接
client = MongoClient(MONGODB_SETTINGS['host'], MONGODB_SETTINGS['port'])
db = client[MONGODB_SETTINGS['db']]
doc_collection = db['document_collection']  # 文档集合


# 例10-7：插入文档
def insert_document(document):
    try:
        result = doc_collection.insert_one(document)
        print(f"文档插入成功，ID：{result.inserted_id}")
        return result
    except Exception as e:
        print(f"插入文档失败：{e}")
        return None


# 例10-8：获取所有文档（去除_id）
def find_all_documents():
    try:
        results = doc_collection.find()
        document_list = []
        for doc in results:
            doc.pop("_id", None)
            document_list.append(doc)
        print(f"获取到{len(document_list)}条文档")
        return document_list
    except Exception as e:
        print(f"获取所有文档失败：{e}")
        return None


# 例10-9：获取单个文档（去除_id）
def find_one_document(condition):
    if not condition:
        print("查询条件不能为空")
        return None
    try:
        result = doc_collection.find_one(condition)
        if not result:
            print("未找到匹配的文档")
            return None
        result.pop("_id", None)
        return result
    except Exception as e:
        print(f"获取单个文档失败：{e}")
        return None


# 例10-10：修改单个文档（补充缺失的print(e)和return None）
def update_one_document(condition, reset):
    try:
        result = doc_collection.update_one(condition, reset)
        if result.modified_count > 0:
            print("文档修改成功")
        else:
            print("未找到匹配的文档或无需修改")
        return result
    except Exception as e:
        print(f"修改文档失败：{e}")
        return None


# 测试
if __name__ == "__main__":
    # 测试插入
    test_doc = {
        "documentId": "doc_001",
        "templateId": "temp_001",
        "content": "测试文档内容",
        "state": "1"
    }
    insert_document(test_doc)

    # 测试查询所有
    all_docs = find_all_documents()
    print("所有文档：", all_docs)

    # 测试查询单个
    one_doc = find_one_document({"documentId": "doc_001"})
    print("单个文档：", one_doc)

    # 测试修改
    update_one_document({"documentId": "doc_001"}, {"$set": {"state": "0"}})

    # 测试修改后查询
    updated_doc = find_one_document({"documentId": "doc_001"})
    print("修改后的文档：", updated_doc)