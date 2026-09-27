"""Multinomial Naive Bayes text classifier (Python 3 standard library only).

Design notes
------------
* All probabilities are computed in the log domain to avoid underflow
  caused by multiplying many small probabilities.
* Unseen (in training) features are handled with Lidstone smoothing
  controlled by ``alpha`` (alpha=1.0 is classic Laplace smoothing), so
  the model never produces a zero probability for a known vocabulary
  token. Tokens that never appeared in training are ignored at predict
  time (they carry no information about any class).
* Training and prediction are fully deterministic: classes and
  vocabulary are iterated in sorted order and only integer counts and
  exact float math are used, so fitting the same data twice yields
  identical parameters and predictions.
"""

import math
from collections import Counter, defaultdict


class MultinomialNaiveBayes:
    """Multinomial Naive Bayes for tokenized text.

    Parameters
    ----------
    alpha : float
        Lidstone smoothing parameter. alpha=1.0 is Laplace smoothing
        (the default, a safe choice for small samples); 0 < alpha < 1
        trusts the observed counts more; alpha > 1 pushes token
        probabilities towards uniform. Must be > 0.
    fit_prior : bool
        If True (default), class priors are the empirical class
        frequencies, which lets an imbalanced training set shift the
        decision towards the majority class. If False, a uniform prior
        is used, which is preferable when the training set's class
        balance does not reflect deployment-time balance.
    """

    def __init__(self, alpha=1.0, fit_prior=True):
        if alpha <= 0:
            raise ValueError("alpha must be > 0")
        self.alpha = float(alpha)
        self.fit_prior = bool(fit_prior)
        # Learned parameters (populated by fit):
        self.classes_ = []          # sorted list of class labels
        self.class_log_prior_ = {}  # label -> log P(class)
        self.feature_log_prob_ = {} # label -> {token: log P(token | class)}
        self.vocabulary_ = set()
        self.class_token_count_ = {}  # label -> total token count

    def fit(self, documents, labels):
        """Train on tokenized documents.

        ``documents`` is an iterable of token iterables, ``labels`` the
        matching class labels. Deterministic for the same input data,
        regardless of document/label ordering.
        """
        documents = list(documents)
        labels = list(labels)
        if len(documents) != len(labels):
            raise ValueError("documents and labels must have the same length")
        if not documents:
            raise ValueError("cannot fit on an empty training set")

        class_doc_count = Counter()
        token_counts = defaultdict(Counter)  # label -> Counter(token)
        vocabulary = set()
        for tokens, label in zip(documents, labels):
            class_doc_count[label] += 1
            for token in tokens:
                token_counts[label][token] += 1
                vocabulary.add(token)

        self.classes_ = sorted(class_doc_count)
        self.vocabulary_ = vocabulary
        n_classes = len(self.classes_)
        n_docs = len(documents)
        n_vocab = len(vocabulary)

        self.class_log_prior_ = {}
        for label in self.classes_:
            if self.fit_prior:
                prior = class_doc_count[label] / n_docs
            else:
                prior = 1.0 / n_classes
            self.class_log_prior_[label] = math.log(prior)

        self.feature_log_prob_ = {}
        self.class_token_count_ = {}
        for label in self.classes_:
            counts = token_counts[label]
            total = sum(counts.values())
            self.class_token_count_[label] = total
            # Smoothed denominator: every vocabulary token gets alpha
            # pseudo-counts, so probabilities stay positive and sum to 1.
            denominator = total + self.alpha * n_vocab
            self.feature_log_prob_[label] = {
                token: math.log((counts.get(token, 0) + self.alpha) / denominator)
                for token in sorted(vocabulary)
            }
        return self

    def decision_function(self, tokens):
        """Return {label: log P(label) + log P(tokens | label)}.

        Tokens not seen during training are ignored. An empty token
        list therefore yields exactly the class log priors.
        """
        if not self.classes_:
            raise ValueError("model is not fitted")
        counts = Counter(t for t in tokens if t in self.vocabulary_)
        scores = {}
        for label in self.classes_:
            log_prob = self.class_log_prior_[label]
            token_log_prob = self.feature_log_prob_[label]
            for token in sorted(counts):
                log_prob += counts[token] * token_log_prob[token]
            scores[label] = log_prob
        return scores

    def predict(self, tokens):
        """Return (best_label, {label: log likelihood}) for one document."""
        scores = self.decision_function(tokens)
        # Sort by (-score, label) so ties break deterministically.
        best = min(scores, key=lambda label: (-scores[label], label))
        return best, scores

    def predict_many(self, documents):
        """Predict a batch of tokenized documents."""
        return [self.predict(tokens) for tokens in documents]


def _demo():
    docs = [
        ["Chinese", "Beijing", "Chinese"],
        ["Chinese", "Chinese", "Shanghai"],
        ["Chinese", "Macao"],
        ["Tokyo", "Japan", "Chinese"],
    ]
    labels = ["c", "c", "c", "j"]
    clf = MultinomialNaiveBayes().fit(docs, labels)
    for tokens in (["Chinese", "Chinese", "Chinese", "Tokyo", "Japan"], []):
        label, scores = clf.predict(tokens)
        print(f"tokens={tokens!r} -> {label!r}")
        for cls in sorted(scores):
            print(f"  log P({cls}|x) ∝ {scores[cls]:.6f}")


if __name__ == "__main__":
    _demo()
