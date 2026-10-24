from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def test_korean_char_tfidf_handles_particles():
    corpus = ["이메일을 적용", "이메일은 적용 대상", "결제 기능"]
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4)).fit_transform(corpus)
    sims = cosine_similarity(vec[0], vec[1:]).ravel()
    assert sims[0] > sims[1]
