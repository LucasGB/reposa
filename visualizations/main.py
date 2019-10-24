import plotly.graph_objects as go 
from plotly.subplots import make_subplots

import pandas as pd

import pymongo

mongo_client = pymongo.MongoClient("mongodb://localhost:27017/")
database = mongo_client["reposadb"]
pull_requests_collection = database["pull_requests"]
repositories_collection = database["repositories"]

def go():
    df1 = pd.read_csv('https://raw.githubusercontent.com/plotly/datasets/718417069ead87650b90472464c7565dc8c2cb1c/sunburst-coffee-flavors-complete.csv')
    df2 = pd.read_csv('https://raw.githubusercontent.com/plotly/datasets/718417069ead87650b90472464c7565dc8c2cb1c/coffee-flavors.csv')

    fig = make_subplots(
        rows = 1, cols = 2,
        column_widths = [0.4, 0.4],
        specs = [[{'type': 'treemap', 'rowspan': 1}, {'type': 'treemap'}]]
    )

    fig.add_trace(
        go.Treemap(
            ids = df1.ids,
            labels = df1.labels,
            parents = df1.parents),
        col = 1, row = 1)

    fig.add_trace(
        go.Treemap(
            ids = df2.ids,
            labels = df2.labels,
            parents = df2.parents,
            maxdepth = 3),
        col = 2, row = 1)

    fig.update_layout(
        margin = {'t':0, 'l':0, 'r':0, 'b':0}
    )

    fig.show()

def query_rejected_prs(repository='facebook/react', n=500):
    query = {"repository_id" : repository, "merged" : False}
    
    rejected_prs = list( pull_requests_collection.find(query).sort('closed_at', pymongo.ASCENDING) )

    print(rejected_prs)

def query_accepted_prs(repository='facebook/react', n=500):
    query = {"repository_id" : repository, "merged" : True}
    
    accepted_prs = list(pull_requests_collection.find(query))

#    print(len(accepted_prs))

if __name__ == '__main__':
    query_rejected_prs()
    query_accepted_prs()