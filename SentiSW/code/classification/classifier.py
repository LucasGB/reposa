import pickle
from SentiSW.code.classification.file import get_training_data
from SentiSW.code.classification.model import create_model_from_training_data
from SentiSW.code.classification.doc_to_vec import DocToVec, default_model_path
from SentiSW.code.classification.preprocess.preprocess import preprocess
from SentiSW.settings import dir_path

#model_path = dir_path + '/data/model/sentimentClassification/classifier.pkl'
#vector_path = dir_path + '/data/model/sentimentClassification/vector.pkl'

model_path = dir_path + '/data/model/sentimentClassification/classifier_sentiSW+_(preds)_no_chinese_chars.pkl'
vector_path = dir_path + '/data/model/sentimentClassification/vector_sentiSW+_(preds)_no_chinese_chars.pkl'

class Classifier:
    def __init__(self, algo='GBT', training_data=None, vector_method='doc2vec', read=False):
        self.vector_method = vector_method
        # read: 直接从硬盘中读模型
        if read:
            with open(model_path, 'rb') as fid:
                self.model = pickle.load(fid)
            with open(vector_path, 'rb') as fid:
                self.vectorizer = pickle.load(fid)
            #self.vec_model = DocToVec(model=default_model_path)
        else:
            if training_data is None:
                training_data = get_training_data()
            model_vectorizer, vec_model = create_model_from_training_data(algo, training_data, vector_method)
            self.model = model_vectorizer['model']
            self.vec_model = vec_model
            if 'vectorizer' in model_vectorizer:
                self.vectorizer = model_vectorizer['vectorizer']
            print("Saving model.")
            self.save_model()

    def get_sentiment_polarity_label(self, text):
        text = preprocess(text)
        if self.vector_method == 'tfidf':
            feature_vector = self.vectorizer.transform([text]).toarray()
        elif self.vector_method == 'doc2vec':
            feature_vector = self.vec_model.get_doc_to_vec(text)
        sentiment_class=self.model.predict(feature_vector)
        return sentiment_class
    
    def get_sentiment_polarity_proba(self, text):
        text = preprocess(text)
        if self.vector_method == 'tfidf':
            feature_vector = self.vectorizer.transform([text]).toarray()
        elif self.vector_method == 'doc2vec':
            feature_vector = self.vec_model.get_doc_to_vec(text)
        sentiment_class=self.model.predict_proba(feature_vector)
        return sentiment_class

    def get_sentiment_polarity_collection(self, texts):
        predictions=[]
        count = 0
        for text in texts:
            comment = preprocess(text)
            if self.vector_method == 'tfidf':
                feature_vector = self.vectorizer.transform([comment]).toarray()
            elif self.vector_method == 'doc2vec' and self.vec_model:
                feature_vector = self.vec_model.get_doc_to_vec(comment)
            sentiment_class = self.model.predict(feature_vector)
            predictions.append(sentiment_class)
            count += 1

        return predictions

    # save model into disk
    def save_model(self):
        with open(model_path, 'wb') as fid:
            print("pickling model")
            pickle.dump(self.model, fid)
            print("pickled")
        with open(vector_path, 'wb') as fid:
            print("pickling vec")
            pickle.dump(self.vectorizer, fid)
            print("pickled")
