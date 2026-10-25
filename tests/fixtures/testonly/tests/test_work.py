from tenacity import retry
@retry
def work():
    pass
