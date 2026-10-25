from tenacity import retry
@retry
def work():
    return fetch()
