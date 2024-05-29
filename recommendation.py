from scipy.sparse import csr_matrix
import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.model_selection import KFold
from sklearn.metrics import mean_squared_error
from sklearn.cluster import KMeans
from mlxtend.frequent_patterns import apriori, association_rules


def create_mappings(df):
    """
    Maps user to movie and movie to user

    Args:
        df: pandas dataframe

    Returns:
        user2movie: dictionary that maps user to movie
        movie2user: dictionary that maps movie to user

    """


    user2movie = df.groupby('userId')['movieId'].unique().to_dict()
    movie2user = df.groupby('movieId')['userId'].unique().to_dict()

    return user2movie, movie2user


def create_utility_matrix(df):
    """
    Creates a pivot table from the original DataFrame. This results
    in a new DataFrame where users and movies are organized along rows
    and columns

    Args:
        df: pandas dataframe

    Returns:
        utility_matrix: utility matrix with information about movie ratings

    """
    utility_matrix = df.pivot_table(index='userId', columns='movieId', values='rating')
    
    # Fill NaN values with 0
    utility_matrix.fillna(0, inplace=True) 

    return utility_matrix


def shrinking_data(n, df):
    user_likes = df.groupby('userId')['movieId'].count()
    top_users = user_likes.nlargest(n).index

    top_users_df = df[df['userId'].isin(top_users)].reset_index(drop=True)

    return top_users_df


def return_datasets(data, kf):

    """
    Combines datsets after KFold method

    Args:
        data: pandas dataframe which is splitted
        kf: kfold

    Returns:
        combined_train_data: all train subsets combined together
        combined_test_data: all test subsets combined together
    """

    data = data.copy()
    all_train_data = []
    all_test_data = []
    
    for train_index, test_index in kf.split(data):
    
        train_data_kf = data.iloc[train_index].copy()
        test_data_kf = data.iloc[test_index].copy()

        all_train_data.append(train_data_kf)
        all_test_data.append(test_data_kf)


    return all_train_data, all_test_data


def train_kmeans_and_predict(train_df, test_df, n_clusters=5):
    """
    Trains KMeans model and predicts ratings for the test data

    Args:
        train_df: list of train subsets for each KFold split
        test_df: list of test subsets for each KFold split
        n_clusters: number of clusters for KMeans

    Returns:
        mse_list: list of mean squared errors for each KFold split
        models: list of trained KMeans models
        train_dfs: list of train dataframes with clusters assigned
    """
    mse_list = []
    models = []
    train_dfs = []

    for i in range(len(train_df)):
        train_data = train_df[i]
        test_data = test_df[i]

        X_train = train_data.drop(columns=['userId', 'movieId', 'rating', 'title'])
        X_test = test_data.drop(columns=['userId', 'movieId', 'rating', 'title'])
        y_test = test_data['rating']

        kmeans = KMeans(n_clusters=n_clusters, random_state=42)
        kmeans.fit(X_train)

        train_data['cluster'] = kmeans.labels_
        
        if 'predicted_rating' in test_data.columns:
            test_data = test_data.drop(columns=['predicted_rating'])
        
        test_data['cluster'] = kmeans.predict(X_test)

        cluster_mean_ratings = train_data.groupby('cluster')['rating'].mean()
        test_data['predicted_rating'] = test_data['cluster'].map(cluster_mean_ratings)

        mse = mean_squared_error(y_test, test_data['predicted_rating'])
        mse_list.append(mse)

        models.append(kmeans)
        train_dfs.append(train_data)

    return mse_list, models, train_dfs

def predict_rating_kmeans(user_id, movie_id, models, train_dfs, movie_data):
    """
    Predicts the rating for a given user and movie

    Args:
        user_id: ID of the user
        movie_id: ID of the movie
        models: list of trained KMeans models
        train_dfs: list of train dataframes with clusters assigned
        movie_data: original movie data to find movie features

    Returns:
        predicted_rating: predicted rating for the given user and movie
    """
    # Find the movie features
    movie_features = movie_data[movie_data['movieId'] == movie_id].drop(columns=['userId', 'movieId', 'rating', 'title'])
    if movie_features.empty:
        return None  # Movie not found

    # Predict cluster for the movie using the first model (for simplicity)
    predicted_cluster = models[0].predict(movie_features)

    # Find the mean rating for the predicted cluster in the train data
    cluster_mean_ratings = train_dfs[0].groupby('cluster')['rating'].mean()
    predicted_rating = cluster_mean_ratings.get(predicted_cluster[0], None)

    return predicted_rating, predicted_cluster, cluster_mean_ratings


def getFrequentItemset(clustered_data, min_support=0.02):
    """
    Finds frequent itemsets from the clustered data

    Args:
        clustered_data: list of train dataframes with clusters assigned
        min_support: minimum support for the frequent itemsets

    Returns:
        frequent_itemsets: list of frequent itemsets found for each train dataframe for each cluster
    """
    frequent_itemsets = []

    for dataset in clustered_data:
        curr_freq_itemset = []
        train_df = dataset.drop(columns=['userId', 'movieId', 'rating', 'title'])

        for cluster in train_df['cluster'].unique():
            freq = apriori(train_df[train_df['cluster']==cluster].drop(columns=['cluster']), min_support, use_colnames=True)
            curr_freq_itemset.append(freq)

        frequent_itemsets.append(curr_freq_itemset)
    
    return frequent_itemsets


def getRules(freq_itemsets, metric='confidence', min_threshold=0.8):
    """
    Generates association rules from the frequent itemsets

    Args:
        freq_itemsets: list of frequent itemsets for each cluster
        metric: metric used to assess the quality of association rules
        min_threshold: minimum threshold which the metric has to fulfill

    Returns:
        rules: list of association rules for each cluster in each group of freq_itemsets
    """

    rules = []
    for set in freq_itemsets:
        curr_rules = []
        for cluster in set:
            assoc_rules = association_rules(cluster, metric, min_threshold)
            curr_rules.append(assoc_rules)

        rules.append(curr_rules)
    
    return rules

def filterRules(userId, movieId, df, cluster, cluster_mean_ratings, rules):
    """
    Filters rules to contain only rules important for a given user and movie based on the rules generated by the Apriori algorithm.

    Args:
        userId: ID of the user
        movieId: ID of the movie
        df: DataFrame containing the user-movie data
        cluster: Cluster predicted for the movie
        rules: Rules generated by the Apriori algorithm

    Returns:
        rating: If the user has already rated the movie, returns the rating
        cluster_mean_ratings: If the user has not rated any movies yet, returns the mean rating of the cluster
        important_rules: Filtered rules

    """
    rating = None

    user_movies = df[df['userId'] == userId]

    if movieId in user_movies['movieId'].values:
        print(f"User {userId} has already rated movie {movieId}")
        rating = user_movies[user_movies['movieId'] == movieId]['rating'].values[0]
        return rating
    
    elif user_movies.empty:
            print(f"User {userId} has not rated any movies yet")
            return cluster_mean_ratings
    
    else:
        user_movies = user_movies.drop(columns = ['userId', 'movieId', 'title'])
        user_movies['genres'] = user_movies.apply(lambda x: list(x.index[x == 1]), axis=1)
        user_movies = user_movies[['rating', 'genres']]
        
    movie_genres = df[df['movieId'] == movieId].drop(columns=['userId', 'movieId', 'rating', 'title']).where(lambda x: x == 1).dropna(axis=1).columns
    
    important_rules = []

    #from cluster and model 0 (simplicity)
    for rule in rules[0][cluster].iterrows():
        if rule[1]['consequents'].issubset(movie_genres):
            for movie in user_movies.iterrows():
                if rule[1]['antecedents'].issubset(movie[1][1]):
                    important_rules.append((rule[1], movie[1][0]))

    return important_rules

def predictRatingRules(important_rules):
    """
    Predicts the rating for a movie based on the rules generated by the Apriori algorithm.

    Args:
        important_rules: Important rules generated by the filterRules function

    Returns:
        predicted_rating: Predicted rating for the movie

    """
    predicted_rating = 0
    sum_confidence = 0

    for rule in important_rules:
        predicted_rating += rule[0]['confidence'] * rule[1]
        sum_confidence += rule[0]['confidence']

    if sum_confidence == 0:
        return 0

    rating = round(predicted_rating/sum_confidence, 2)
    return rating

def roundRating(rating):
    """
    Rounds the rating to the nearest half integer
    
    Args:
        rating: rating to be rounded to 2 decimal places
    
    Returns:
        rounded_rating: rounded rating
    """

    decimal = rating - int(rating)
    if decimal < 0.25:
        return int(rating)
    elif decimal < 0.75:
        return int(rating) + 0.5
    else:
        return int(rating) + 1

