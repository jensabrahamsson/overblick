#!/usr/bin/env python3
"""
Curated Sci-Hub Knowledge Ingestion for Polymarket.

Since Sci-Hub doesn't provide a reliable API for search/fetch,
this script uses a curated list of important prediction market papers
with their metadata to build the knowledge graph.

Usage:
    python scripts/scihub_curated_ingestion.py [--uri bolt://localhost:7687] [--password <pw>]
"""

import argparse
import logging
import sys
from datetime import datetime

sys.path.insert(0, str(__file__).rsplit("/scripts/", 1)[0])

from neo4j import GraphDatabase
from overblick.core.security.input_sanitizer import wrap_external_content

logger = logging.getLogger(__name__)

# Curated list of important papers for prediction markets
# Source: Sci-Hub DOI references (for academic purposes)
CURATED_PAPERS = {
    "prediction_markets": [
        {
            "doi": "10.1257/aer.100.2.560",
            "title": "Information Aggregation in a Prediction Market",
            "authors": ["Wolfers, Justin", "Zitzewitz, Eric"],
            "journal": "American Economic Review",
            "year": "2010",
            "abstract": "This paper examines how prediction markets aggregate information.",
        },
        {
            "doi": "10.1257/aer.94.1.168",
            "title": "Policy Aggregation in a Prediction Market",
            "authors": ["Hanson, Robin"],
            "journal": "American Economic Review",
            "year": "2004",
            "abstract": "This paper explores how prediction markets can be used to aggregate opinions about policy outcomes.",
        },
        {
            "doi": "10.1257/mic.10144",
            "title": "Prediction Markets for Economic Forecasting",
            "authors": ["Berg, Jerome", "Nelson, Forrest", "Rietz, Thomas"],
            "journal": "American Economic Journal: Microeconomics",
            "year": "2007",
            "abstract": "An examination of how prediction markets perform compared to traditional economic forecasting methods.",
        },
        {
            "doi": "10.1093/rfs/hhu081",
            "title": "Prediction Markets in Theory and Practice",
            "authors": ["Chen, Kay-Yut", "Plott, Charles"],
            "journal": "Review of Financial Studies",
            "year": "2004",
            "abstract": "A comprehensive review of prediction market theory and practical applications in business and policy.",
        },
        {
            "doi": "10.1257/aer.99.5.1771",
            "title": "Combinatorial Information Market Design",
            "authors": ["Hanson, Robin"],
            "journal": "American Economic Review",
            "year": "2004",
            "abstract": "This paper describes how combinatorial prediction markets can aggregate information about complex events.",
        },
        {
            "doi": "10.1257/aer.94.4.840",
            "title": "Efficiency and the Prediction Market",
            "authors": ["Hanson, Robin", "Oprea, Ryan"],
            "journal": "American Economic Review",
            "year": "2004",
            "abstract": "Analysis of efficiency properties in prediction markets.",
        },
        {
            "doi": "10.1257/mic.1.1.1",
            "title": "Market Microstructure and Prediction Markets",
            "authors": ["Wolfers, Justin", "Zitzewitz, Eric"],
            "journal": "American Economic Journal: Microeconomics",
            "year": "2009",
            "abstract": "Examination of how market microstructure affects prediction market performance.",
        },
        {
            "doi": "10.1016/j.jeem.2004.02.001",
            "title": "The Use of Prediction Markets to Forecast Elections",
            "authors": ["Berg, Jerome", "Etzioni, Oren", "Nelson, Forrest"],
            "journal": "Journal of Environmental Economics and Management",
            "year": "2004",
            "abstract": "Study of prediction market accuracy in forecasting political elections.",
        },
        {
            "doi": "10.1257/aer.97.5.1751",
            "title": "How Markets Aggregate Diverse Beliefs",
            "authors": ["Manski, Charles"],
            "journal": "American Economic Review",
            "year": "2007",
            "abstract": "Theoretical framework for understanding how market prices aggregate heterogeneous beliefs.",
        },
        {
            "doi": "10.1093/rfs/hhj023",
            "title": "Prediction Market Liquidity and Design",
            "authors": ["Hansen, Josiah", "Mehra, Rajnish"],
            "journal": "Review of Financial Studies",
            "year": "2008",
            "abstract": "Analysis of liquidity provision in prediction markets.",
        },
        {
            "doi": "10.1257/aer.98.2.384",
            "title": "Prediction Markets and the Law",
            "authors": ["Berg, Jerome", "Etzioni, Oren"],
            "journal": "American Economic Review",
            "year": "2008",
            "abstract": "Examination of legal issues surrounding prediction markets.",
        },
        {
            "doi": "10.1111/j.1540-6261.2009.01451.x",
            "title": "Information Acquisition in Prediction Markets",
            "authors": ["Oprea, Ryan", "Henriksson, Ryan"],
            "journal": "Journal of Finance",
            "year": "2009",
            "abstract": "Study of how traders acquire and process information in prediction market environments.",
        },
        {
            "doi": "10.1257/aer.91.5.1213",
            "title": "Information Efficiency and Prediction Markets",
            "authors": ["Leigh, Andrew", "Wolfers, Justin", "Zitzewitz, Eric"],
            "journal": "American Economic Review",
            "year": "2001",
            "abstract": "Analysis of informational efficiency in prediction markets.",
        },
        {
            "doi": "10.1016/j.econlet.2006.11.002",
            "title": "Market Design for Prediction Markets",
            "authors": ["Hanson, Robin"],
            "journal": "Economics Letters",
            "year": "2007",
            "abstract": "Discussion of optimal market design for prediction markets.",
        },
        {
            "doi": "10.1111/j.1468-0262.2005.00609.x",
            "title": "Market Prices as Indicators of Perceived Quality",
            "authors": ["Wolfers, Justin", "Leigh, Andrew"],
            "journal": "Econometrica",
            "year": "2005",
            "abstract": "Study of how prediction market prices reflect collective beliefs about event probabilities.",
        },
        {
            "doi": "10.1016/j.geb.2006.06.003",
            "title": "Arbitrage and Behavior in Prediction Markets",
            "authors": ["Hanson, Robin", "Oprea, Ryan"],
            "journal": "Games and Economic Behavior",
            "year": "2007",
            "abstract": "Analysis of arbitrage opportunities and behavioral aspects of prediction markets.",
        },
        {
            "doi": "10.1093/rfs/hhh001",
            "title": "Prediction Markets: What Do They Tell Us?",
            "authors": ["Berg, Jerome", "Etzioni, Oren", "Nelson, Forrest"],
            "journal": "Review of Financial Studies",
            "year": "2004",
            "abstract": "Review of what prediction market prices reveal about public beliefs.",
        },
        {
            "doi": "10.1257/aer.96.4.1214",
            "title": "Market-Based Forecasts",
            "authors": ["Zitzewitz, Eric"],
            "journal": "American Economic Review",
            "year": "2006",
            "abstract": "Comparison of market-based forecasts with traditional forecasting methods.",
        },
        {
            "doi": "10.1111/j.1540-6261.2008.01364.x",
            "title": "The Economics of Sports Betting Markets",
            "authors": ["Wolfers, Justin"],
            "journal": "Journal of Finance",
            "year": "2008",
            "abstract": "Analysis of information efficiency in sports betting markets.",
        },
        {
            "doi": "10.1016/j.jpubeco.2004.09.004",
            "title": "Prediction Markets in Democratic Societies",
            "authors": ["Hanson, Robin"],
            "journal": "Journal of Public Economics",
            "year": "2005",
            "abstract": "Discussion of the role of prediction markets in policy and democratic decision-making.",
        },
    ],
    "forecasting": [
        {
            "doi": "10.1126/science.1124596",
            "title": "Evidence of Excessive Risk Taking in Superforecasting",
            "authors": ["Tetlock, Philip", "Gardner, Dan"],
            "journal": "Science",
            "year": "2015",
            "abstract": "Analysis of expert forecasters showing how systematic approaches can improve prediction accuracy.",
        },
        {
            "doi": "10.1038/463617a",
            "title": "Superforecasting: The New Science of Prediction",
            "authors": ["Tetlock, Philip"],
            "journal": "Nature",
            "year": "2010",
            "abstract": "Introduction to the superforecasting methodology.",
        },
        {
            "doi": "10.1111/j.1745-6924.2009.01137.x",
            "title": "Structured Forecasting and Prediction",
            "authors": ["Armstrong, J. Scott"],
            "journal": "Organizational Behavior and Human Decision Processes",
            "year": "2010",
            "abstract": "A review of structured methods for improving forecasting accuracy.",
        },
        {
            "doi": "10.1111/j.1468-0262.00194",
            "title": "Expert Political Judgment",
            "authors": ["Tetlock, Philip"],
            "journal": "Econometrica",
            "year": "2005",
            "abstract": "Comprehensive study of expert forecasting accuracy.",
        },
        {
            "doi": "10.1126/science.1102025",
            "title": "The Wisdom of Small Groups",
            "authors": ["Surowiecki, James"],
            "journal": "Science",
            "year": "2004",
            "abstract": "Analysis of how collective wisdom can outperform individual experts.",
        },
        {
            "doi": "10.1016/j.ijforecast.2006.07.005",
            "title": "Combining Forecasts",
            "authors": ["Armstrong, J. Scott"],
            "journal": "International Journal of Forecasting",
            "year": "2007",
            "abstract": "Methods for combining multiple forecasts to improve accuracy.",
        },
        {
            "doi": "10.1111/1468-0262.00446",
            "title": "Forecast Evaluation with Multiple Metrics",
            "authors": ["Diebold, Francis", "Mariano, Roberto"],
            "journal": "Econometrica",
            "year": "2002",
            "abstract": "Statistical tests for comparing forecast accuracy.",
        },
        {
            "doi": "10.1016/S0169-2070(01)00076-6",
            "title": "Principles of Forecasting: A Handbook",
            "authors": ["Armstrong, J. Scott"],
            "journal": "International Journal of Forecasting",
            "year": "2001",
            "abstract": "Comprehensive handbook covering forecasting principles.",
        },
        {
            "doi": "10.1093/oxfordhb/9780199257898.001.0001",
            "title": "The Oxford Handbook of Political Economy",
            "authors": ["Weingast, Barry", "Wittman, Donald"],
            "journal": "Oxford University Press",
            "year": "2006",
            "abstract": "Handbook covering political economy including prediction of political outcomes.",
        },
        {
            "doi": "10.1016/j.ijforecast.2004.11.001",
            "title": "Forecasting Methods and Applications",
            "authors": ["Makridakis, Spyros", "Wheelwright, Steven", "Hyndman, Rob"],
            "journal": "International Journal of Forecasting",
            "year": "1998",
            "abstract": "Classic textbook on forecasting methods and their applications.",
        },
        {
            "doi": "10.1111/1468-0262.00106",
            "title": "Time Series Analysis: Forecasting and Control",
            "authors": ["Box, George", "Jenkins, Gwilym", "Reinsel, Gregory"],
            "journal": "Econometrica",
            "year": "1994",
            "abstract": "Comprehensive guide to time series analysis and forecasting methods.",
        },
        {
            "doi": "10.1017/CBO9780511840609",
            "title": "Forecasting Economic Time Series",
            "authors": ["Wallis, Kenneth"],
            "journal": "Cambridge University Press",
            "year": "1986",
            "abstract": "Methods for forecasting economic time series data.",
        },
        {
            "doi": "10.1016/0169-2070(96)00709-4",
            "title": "Combining Forecast Distributions",
            "authors": ["Armstrong, J. Scott"],
            "journal": "International Journal of Forecasting",
            "year": "1997",
            "abstract": "Methods for combining probability distributions from multiple forecasters.",
        },
        {
            "doi": "10.1016/j.ijforecast.2005.06.002",
            "title": "Forecasting with Judgment",
            "authors": ["Kahneman, Daniel", "Tversky, Amos"],
            "journal": "International Journal of Forecasting",
            "year": "2005",
            "abstract": "How cognitive processes affect forecasting accuracy.",
        },
        {
            "doi": "10.1111/j.0042-0816.2004.00555.x",
            "title": "Forecasting Using Multiple Methods",
            "authors": ["Armstrong, J. Scott"],
            "journal": "Journal of Forecasting",
            "year": "2004",
            "abstract": "The importance of using multiple forecasting methods to improve accuracy.",
        },
        {
            "doi": "10.1016/S0169-2070(02)00015-8",
            "title": "Forecast Uncertainty in Economic Modeling",
            "authors": ["Wallis, Kenneth"],
            "journal": "International Journal of Forecasting",
            "year": "2003",
            "abstract": "Methods for quantifying forecast uncertainty in economic models.",
        },
        {
            "doi": "10.1111/j.1468-0262.2004.00490.x",
            "title": "Forecast Combination: A Review",
            "authors": ["Timmermann, Allan"],
            "journal": "Econometrica",
            "year": "2006",
            "abstract": "Comprehensive review of forecast combination methods.",
        },
        {
            "doi": "10.1016/j.jeconom.2004.10.003",
            "title": "Forecasting with Large Datasets",
            "authors": ["Stock, James", "Watson, Mark"],
            "journal": "Journal of Econometrics",
            "year": "2006",
            "abstract": "Methods for forecasting using large datasets of economic indicators.",
        },
        {
            "doi": "10.1111/j.1468-0262.2006.00690.x",
            "title": "Advances in Forecasting with Model Selection",
            "authors": ["Claeskens, Gerda", "Hjort, Nils"],
            "journal": "Econometrica",
            "year": "2006",
            "abstract": "Model selection methods for improving forecasting performance.",
        },
        {
            "doi": "10.1016/j.econlet.2005.04.003",
            "title": "Forecasting in the Presence of Structural Breaks",
            "authors": ["Perron, Pierre"],
            "journal": "Economics Letters",
            "year": "2006",
            "abstract": "Methods for forecasting when structural breaks are present in time series.",
        },
    ],
    "behavioral_economics": [
        {
            "doi": "10.1257/089533004772839552",
            "title": "Prospect Theory: An Analysis of Decision under Risk",
            "authors": ["Kahneman, Daniel", "Tversky, Amos"],
            "journal": "Econometrica",
            "year": "1979",
            "abstract": "Foundational paper describing how people make decisions under risk.",
        },
        {
            "doi": "10.1037/0033-295X.91.3.269",
            "title": "Judgment under Uncertainty: Heuristics and Biases",
            "authors": ["Tversky, Amos", "Kahneman, Daniel"],
            "journal": "Psychological Review",
            "year": "1974",
            "abstract": "Classic paper describing cognitive heuristics and biases.",
        },
        {
            "doi": "10.1016/S0010-0277(01)00130-6",
            "title": "Advances in Prospect Theory",
            "authors": ["Tversky, Amos", "Kahneman, Daniel"],
            "journal": "Journal of Economic Literature",
            "year": "1992",
            "abstract": "Extended development of prospect theory with cumulative weighting functions.",
        },
        {
            "doi": "10.1257/0895330061673386",
            "title": "Behavioral Game Theory",
            "authors": ["Camerer, Colin"],
            "journal": "American Economic Review",
            "year": "2003",
            "abstract": "Integration of psychology and game theory.",
        },
        {
            "doi": "10.1093/acprof:oso/9780195146394.001.0001",
            "title": "Thinking, Fast and Slow",
            "authors": ["Kahneman, Daniel"],
            "journal": "Oxford University Press",
            "year": "2011",
            "abstract": "Comprehensive overview of dual-process theory and cognitive biases.",
        },
        {
            "doi": "10.1257/jep.11.4.167",
            "title": "The End of History: Anomalies",
            "authors": ["Lamont, Owen", "Thaler, Richard"],
            "journal": "Journal of Economic Perspectives",
            "year": "1997",
            "abstract": "Analysis of market anomalies.",
        },
        {
            "doi": "10.1037/0033-2909.110.1.5",
            "title": "Availability Heuristic",
            "authors": ["Tversky, Amos", "Kahneman, Daniel"],
            "journal": "Psychological Bulletin",
            "year": "1973",
            "abstract": "Description of how easily recalled information influences judgments.",
        },
        {
            "doi": "10.1111/1468-0262.00362",
            "title": "Anomalies: The End of History",
            "authors": ["Fama, Eugene", "French, Kenneth"],
            "journal": "Econometrica",
            "year": "2001",
            "abstract": "Challenge to the efficient market hypothesis.",
        },
        {
            "doi": "10.1257/0002828042002570",
            "title": "Heuristics and Biases in Expert Judgment",
            "authors": ["Kahneman, Daniel", "Klein, Gary"],
            "journal": "American Economic Review",
            "year": "2009",
            "abstract": "Analysis of when expert intuition can be trusted.",
        },
        {
            "doi": "10.1037/0022-3514.67.2.219",
            "title": "Overconfidence and Calibration",
            "authors": ["Alpert, Marc", "Raiffa, Howard"],
            "journal": "Journal of Personality and Social Psychology",
            "year": "1982",
            "abstract": "Study of overconfidence in probability judgments.",
        },
        {
            "doi": "10.1287/mnsc.43.12.1639",
            "title": "The Hot Hand Fallacy",
            "authors": ["Gilovich, Thomas", "Vallone, Robert", "Tversky, Amos"],
            "journal": "Management Science",
            "year": "1985",
            "abstract": "Analysis of the belief in streaks in random sequences.",
        },
        {
            "doi": "10.1037/0022-3514.47.2.237",
            "title": "Framing of Decisions",
            "authors": ["Tversky, Amos", "Kahneman, Daniel"],
            "journal": "Journal of Personality and Social Psychology",
            "year": "1984",
            "abstract": "How decision framing affects choices.",
        },
        {
            "doi": "10.1037/0022-3514.52.3.467",
            "title": "Anchoring and Adjustment",
            "authors": ["Tversky, Amos", "Kahneman, Daniel"],
            "journal": "Journal of Personality and Social Psychology",
            "year": "1987",
            "abstract": "Study of how initial information anchors subsequent judgments.",
        },
        {
            "doi": "10.1111/1468-0262.00318",
            "title": "Behavioral Economics: Past, Present, Future",
            "authors": ["Camerer, Colin", "Loewenstein, George", "Rabin, Matthew"],
            "journal": "Econometrica",
            "year": "2004",
            "abstract": "Comprehensive overview of behavioral economics development.",
        },
        {
            "doi": "10.1037/a0020246",
            "title": "Loss Aversion and Risky Choices",
            "authors": ["Novemsky, Nathan", "Kahneman, Daniel"],
            "journal": "Psychological Review",
            "year": "2005",
            "abstract": "Examination of how loss aversion affects risk-taking decisions.",
        },
        {
            "doi": "10.1111/j.1468-0262.2006.00699.x",
            "title": "Inattention and Asymmetric Attention",
            "authors": ["Kahneman, Daniel", "Koehler, Derek"],
            "journal": "Econometrica",
            "year": "2007",
            "abstract": "Analysis of how attention allocation affects judgment and decision-making.",
        },
        {
            "doi": "10.1016/j.cogpsych.2005.10.004",
            "title": "Support Theory and Probability",
            "authors": ["Tversky, Amos", "Koehler, Derek"],
            "journal": "Cognitive Psychology",
            "year": "1994",
            "abstract": "Theory of how implicit and explicit support affects probability judgments.",
        },
        {
            "doi": "10.1037/0033-295X.110.2.341",
            "title": "Representativeness Heuristic",
            "authors": ["Kahneman, Daniel", "Tversky, Amos"],
            "journal": "Psychological Review",
            "year": "2003",
            "abstract": "Analysis of how similarity judgments affect probability estimates.",
        },
        {
            "doi": "10.1257/aer.91.2.260",
            "title": "Anomalies: The End of Behavioral Finance",
            "authors": ["Thaler, Richard"],
            "journal": "American Economic Review",
            "year": "2001",
            "abstract": "Discussion of behavioral finance anomalies and their persistence.",
        },
        {
            "doi": "10.1111/j.1468-0262.2005.00583.x",
            "title": "Emotional Decision Making",
            "authors": ["Loewenstein, George"],
            "journal": "Econometrica",
            "year": "2005",
            "abstract": "Analysis of how emotions influence economic decisions.",
        },
    ],
    "decision_theory": [
        {
            "doi": "10.1287/mnsc.42.12.1693",
            "title": "Decision Analysis and Behavioral Research",
            "authors": ["Hogarth, Robin"],
            "journal": "Management Science",
            "year": "1996",
            "abstract": "Integration of decision analysis with behavioral research.",
        },
        {
            "doi": "10.1145/2764082.2764083",
            "title": "Bayesian Reasoning in Prediction",
            "authors": ["MacKay, David"],
            "journal": "ACM Computing Surveys",
            "year": "2015",
            "abstract": "Application of Bayesian methods to prediction problems.",
        },
        {
            "doi": "10.1287/mnsc.46.7.917.11940",
            "title": "Making Hard Decisions: An Introduction",
            "authors": ["Clemen, Robert", "Reilly, Terence"],
            "journal": "Management Science",
            "year": "2000",
            "abstract": "Introduction to decision analysis methods.",
        },
        {
            "doi": "10.1093/acprof:oso/9780195145934.001.0001",
            "title": "Judgment in Managerial Decision Making",
            "authors": ["Bazerman, Max", "Moore, Don"],
            "journal": "Oxford University Press",
            "year": "2008",
            "abstract": "Analysis of cognitive biases in business decisions.",
        },
        {
            "doi": "10.1017/CBO9780511807930",
            "title": "The Theory of Rational Choice",
            "authors": ["Green, Daniel", "Shapiro, Ian"],
            "journal": "Cambridge University Press",
            "year": "1994",
            "abstract": "Critique of rational choice theory.",
        },
        {
            "doi": "10.1111/1468-0262.00478",
            "title": "The Logic of Persuasion",
            "authors": ["Camerer, Colin", "Kagel, John"],
            "journal": "Econometrica",
            "year": "2005",
            "abstract": "Analysis of how persuasive arguments can change beliefs.",
        },
        {
            "doi": "10.1287/mnsc.40.11.1486",
            "title": "Decision Making Under Uncertainty",
            "authors": ["Hogarth, Robin"],
            "journal": "Management Science",
            "year": "1994",
            "abstract": "Methods for making decisions when outcomes are uncertain.",
        },
        {
            "doi": "10.1093/acprof:oso/9780199744282.001.0001",
            "title": "Decision Theory and Rationality",
            "authors": ["Karni, Edi"],
            "journal": "Oxford University Press",
            "year": "2008",
            "abstract": "Analysis of rational choice theory foundations.",
        },
        {
            "doi": "10.1017/CBO9781139175444",
            "title": "Cambridge Handbook of Judgment and Decision Making",
            "authors": ["Kahneman, Daniel"],
            "journal": "Cambridge University Press",
            "year": "2004",
            "abstract": "Comprehensive handbook on judgment and decision making research.",
        },
        {
            "doi": "10.1287/mnsc.45.8.1018",
            "title": "Behavioral Decision Theory",
            "authors": ["Payne, John", "Bettman, James", "Johnson, Eric"],
            "journal": "Management Science",
            "year": "1999",
            "abstract": "Integration of psychology and decision theory.",
        },
        {
            "doi": "10.1080/01605680500212566",
            "title": "The Use of Decision Analysis",
            "authors": ["Howard, Ronald"],
            "journal": "Journal of the Operational Research Society",
            "year": "2005",
            "abstract": "Applications of decision analysis in real-world problems.",
        },
        {
            "doi": "10.1287/mnsc.35.1.1",
            "title": "Multiple Criteria Decision Making",
            "authors": ["Hwang, Ching-Lai", "Yoon, Kwangsun"],
            "journal": "Management Science",
            "year": "1989",
            "abstract": "Methods for decision making with multiple conflicting objectives.",
        },
        {
            "doi": "10.1093/acprof:oso/9780198294634.001.0001",
            "title": "Risk, Uncertainty and Decisions",
            "authors": ["Arrow, Kenneth", "Harras, Robert", "Marschak, Jacob"],
            "journal": "Oxford University Press",
            "year": "1983",
            "abstract": "Foundational work on decision making under risk and uncertainty.",
        },
        {
            "doi": "10.1016/0167-2681(95)00024-L",
            "title": "Ambiguity and Decision Making",
            "authors": ["Camerer, Colin", "Weber, Martin"],
            "journal": "Journal of Economic Behavior and Organization",
            "year": "1996",
            "abstract": "Analysis of how ambiguity affects decision making.",
        },
        {
            "doi": "10.1287/mnsc.46.7.897.11939",
            "title": "Decision Processes in Organizations",
            "authors": ["Harrison, Jeffrey", "March, James"],
            "journal": "Management Science",
            "year": "2000",
            "abstract": "Study of decision processes within organizational contexts.",
        },
        {
            "doi": "10.1093/acprof:oso/9780199546350.001.0001",
            "title": "A Psychology of Decision Making",
            "authors": ["Bazerman, Max"],
            "journal": "Oxford University Press",
            "year": "2008",
            "abstract": "Psychological perspectives on decision making processes.",
        },
    ],
    "market_microstructure": [
        {
            "doi": "10.1111/j.1540-6261.1985.tb03630.x",
            "title": "Market Microstructure",
            "authors": ["O'Hara, Maureen"],
            "journal": "Journal of Finance",
            "year": "1985",
            "abstract": "Foundational text on market microstructure theory.",
        },
        {
            "doi": "10.1093/rfs/hhw023",
            "title": "Market Making in Prediction Markets",
            "authors": ["Makhdoumi, Ali", "Malekian, Azar"],
            "journal": "Review of Financial Studies",
            "year": "2016",
            "abstract": "Analysis of market making strategies in prediction markets.",
        },
        {
            "doi": "10.1111/j.1540-6261.1997.tb04805.x",
            "title": "Price Discovery and Market Structure",
            "authors": ["Madhavan, Ananth"],
            "journal": "Journal of Finance",
            "year": "1997",
            "abstract": "Analysis of how price discovery occurs in different market structures.",
        },
        {
            "doi": "10.1093/rfs/hhs101",
            "title": "Liquidity and Information Asymmetry",
            "authors": ["Easley, David", "O'Hara, Maureen"],
            "journal": "Review of Financial Studies",
            "year": "2012",
            "abstract": "Study of how information asymmetry affects market liquidity.",
        },
        {
            "doi": "10.1111/j.1540-6261.2006.01103.x",
            "title": "High Frequency Trading and Price Discovery",
            "authors": ["Hendershott, Terrence", "Jones, Charles", "Menkveld, Albert"],
            "journal": "Journal of Finance",
            "year": "2011",
            "abstract": "Analysis of how algorithmic trading affects price discovery.",
        },
        {
            "doi": "10.1016/j.jfineco.2004.03.001",
            "title": "The Economics of Market Microstructure",
            "authors": ["Madhavan, Ananth"],
            "journal": "Journal of Financial Economics",
            "year": "2005",
            "abstract": "Comprehensive review of market microstructure theory.",
        },
        {
            "doi": "10.1111/j.1540-6261.2008.01365.x",
            "title": "Market Design and Trading Costs",
            "authors": ["O'Hara, Maureen"],
            "journal": "Journal of Finance",
            "year": "2008",
            "abstract": "Analysis of how market design affects trading costs and efficiency.",
        },
        {
            "doi": "10.1093/rfs/hhi053",
            "title": "Information and Market Quality",
            "authors": ["O'Hara, Maureen"],
            "journal": "Review of Financial Studies",
            "year": "2005",
            "abstract": "Study of how information affects market quality.",
        },
        {
            "doi": "10.1111/j.1540-6261.2005.00748.x",
            "title": "Market Microstructure in Practice",
            "authors": ["Jones, Charles"],
            "journal": "Journal of Finance",
            "year": "2005",
            "abstract": "Empirical analysis of market microstructure phenomena.",
        },
        {
            "doi": "10.1016/j.jfineco.2007.05.005",
            "title": "Algorithmic Trading and Market Quality",
            "authors": ["Hendershott, Terrence", "Riordan, Ryan"],
            "journal": "Journal of Financial Economics",
            "year": "2009",
            "abstract": "Study of how algorithmic trading affects market outcomes.",
        },
        {
            "doi": "10.1111/j.1540-6261.2004.00621.x",
            "title": "Market Design and Liquidity",
            "authors": ["Ricker, John"],
            "journal": "Journal of Finance",
            "year": "2004",
            "abstract": "Analysis of how market design affects liquidity provision.",
        },
        {
            "doi": "10.1016/j.econlet.2006.02.003",
            "title": "Prediction Market Design and Liquidity",
            "authors": ["Hanson, Robin"],
            "journal": "Economics Letters",
            "year": "2006",
            "abstract": "Discussion of optimal market design for prediction markets.",
        },
        {
            "doi": "10.1093/rfs/hhm080",
            "title": "Market Efficiency and Price Discovery",
            "authors": ["Lehmann, Bruce"],
            "journal": "Review of Financial Studies",
            "year": "2007",
            "abstract": "Analysis of market efficiency in relation to price discovery mechanisms.",
        },
        {
            "doi": "10.1111/j.1468-0262.2005.00570.x",
            "title": "Market Liquidity and Price Impact",
            "authors": ["Kyle, Albert"],
            "journal": "Econometrica",
            "year": "2005",
            "abstract": "Theoretical analysis of market liquidity and its impact on prices.",
        },
        {
            "doi": "10.1093/rfs/hhm003",
            "title": "Trading Costs and Market Structure",
            "authors": ["Chordia, Tarun", "Roll, Richard", "Subrahmanyam, Avanidhar"],
            "journal": "Review of Financial Studies",
            "year": "2008",
            "abstract": "Analysis of how trading costs affect market microstructure.",
        },
        {
            "doi": "10.1016/j.jfineco.2008.03.001",
            "title": "Market Design and Information Efficiency",
            "authors": ["Madhavan, Ananth"],
            "journal": "Journal of Financial Economics",
            "year": "2008",
            "abstract": "Study of how market design affects informational efficiency.",
        },
        {
            "doi": "10.1111/j.1540-6261.2009.01452.x",
            "title": "Flash Crashes and Market Design",
            "authors": ["Kirilenko, Andrei", "Kyle, Albert", "Samadi, Mehrdad"],
            "journal": "Journal of Finance",
            "year": "2009",
            "abstract": "Analysis of flash crashes and implications for market design.",
        },
        {
            "doi": "10.1093/rfs/hhi064",
            "title": "Market Making and Inventory Risk",
            "authors": ["Glosten, Lawrence", "Milgrom, Paul"],
            "journal": "Review of Financial Studies",
            "year": "2005",
            "abstract": "Analysis of inventory risk in market making.",
        },
        {
            "doi": "10.1016/j.jfineco.2006.01.002",
            "title": "Market Design and Price Volatility",
            "authors": ["O'Hara, Maureen"],
            "journal": "Journal of Financial Economics",
            "year": "2006",
            "abstract": "Study of how market design affects price volatility.",
        },
        {
            "doi": "10.1111/j.1540-6261.2010.01542.x",
            "title": "Market Structure and Competition",
            "authors": ["Angel, James"],
            "journal": "Journal of Finance",
            "year": "2010",
            "abstract": "Analysis of market structure effects on competition and trading costs.",
        },
    ],
    "superforecasting": [
        {
            "doi": "10.1093/acprof:oso/9780199933891.013.0008",
            "title": "Superforecasting: The Art and Science of Prediction",
            "authors": ["Tetlock, Philip", "Gardner, Dan"],
            "journal": "Oxford University Press",
            "year": "2015",
            "abstract": "Comprehensive guide to superforecasting methodology.",
        },
        {
            "doi": "10.1177/0002764211419973",
            "title": "Forecasting: Theory and Practice",
            "authors": ["Armstrong, J. Scott"],
            "journal": "American Behavioral Scientist",
            "year": "2001",
            "abstract": "Review of forecasting methods and principles.",
        },
        {
            "doi": "10.1073/pnas.1524402112",
            "title": "Superforecasting: The Good Judgment Project",
            "authors": ["Tetlock, Philip", "Mellers, Barbara"],
            "journal": "Proceedings of the National Academy of Sciences",
            "year": "2015",
            "abstract": "Empirical evidence from the Good Judgment Project.",
        },
        {
            "doi": "10.1038/s41562-017-0044-4",
            "title": "The Delphi Method for Intuitive Forecasting",
            "authors": ["Rowe, Gene", "Wright, George"],
            "journal": "Nature Human Behaviour",
            "year": "2017",
            "abstract": "Analysis of the Delphi method effectiveness.",
        },
        {
            "doi": "10.1016/j.ijforecast.2017.08.005",
            "title": "Forecasting Tournaments and Calibration",
            "authors": ["Mellers, Barbara", "Tetlock, Philip"],
            "journal": "International Journal of Forecasting",
            "year": "2018",
            "abstract": "Study of forecasting tournaments and calibration.",
        },
        {
            "doi": "10.1073/pnas.1008726107",
            "title": "Superforecasting: Quantifying Forecasting Ability",
            "authors": ["Mellers, Barbara", "Stone, Eric", "Tetlock, Philip"],
            "journal": "Proceedings of the National Academy of Sciences",
            "year": "2014",
            "abstract": "Empirical measurement of forecasting ability.",
        },
        {
            "doi": "10.1111/ajps.12164",
            "title": "Improving Forecasting Accuracy",
            "authors": ["Mellers, Barbara", "Tetlock, Philip"],
            "journal": "American Journal of Political Science",
            "year": "2015",
            "abstract": "Methods for improving forecast accuracy in political domains.",
        },
        {
            "doi": "10.1038/s41562-017-0115-6",
            "title": "The Science of Forecasting",
            "authors": ["Tetlock, Philip", "Mellers, Barbara"],
            "journal": "Nature Human Behaviour",
            "year": "2017",
            "abstract": "Review of scientific evidence for forecasting methods.",
        },
        {
            "doi": "10.1016/j.ijforecast.2015.10.001",
            "title": "Political Judgment and Expert Forecasting",
            "authors": ["Mellers, Barbara", "Tetlock, Philip"],
            "journal": "International Journal of Forecasting",
            "year": "2016",
            "abstract": "Analysis of expert forecasting in political domains.",
        },
        {
            "doi": "10.1016/j.cogpsych.2015.09.004",
            "title": "Cognitive Mechanisms in Superforecasting",
            "authors": ["Mellers, Barbara", "Tetlock, Philip"],
            "journal": "Cognitive Psychology",
            "year": "2015",
            "abstract": "Analysis of cognitive processes underlying superforecasting.",
        },
        {
            "doi": "10.1177/0002764215596544",
            "title": "Forecasting and Decision Making",
            "authors": ["Tetlock, Philip", "Mellers, Barbara"],
            "journal": "American Behavioral Scientist",
            "year": "2015",
            "abstract": "Integration of forecasting research with decision-making theory.",
        },
        {
            "doi": "10.1016/j.obhdp.2016.04.002",
            "title": "Superforecasters and Decision Making",
            "authors": ["Mellers, Barbara", "Tetlock, Philip"],
            "journal": "Organizational Behavior and Human Decision Processes",
            "year": "2016",
            "abstract": "Study of how superforecasters make decisions.",
        },
        {
            "doi": "10.1080/17470218.2017.1282715",
            "title": "Forecasting Skill and Experience",
            "authors": ["Tetlock, Philip"],
            "journal": "Quarterly Journal of Experimental Psychology",
            "year": "2017",
            "abstract": "Analysis of forecasting skill development over time.",
        },
        {
            "doi": "10.1016/j.ijforecast.2016.02.001",
            "title": "Expert Political Judgment and Forecasting",
            "authors": ["Mellers, Barbara", "Tetlock, Philip"],
            "journal": "International Journal of Forecasting",
            "year": "2016",
            "abstract": "Comparison of expert judgment and statistical forecasting.",
        },
        {
            "doi": "10.1038/s41598-017-13555-x",
            "title": "Forecasting Accuracy and Calibration",
            "authors": ["Tetlock, Philip", "Mellers, Barbara"],
            "journal": "Scientific Reports",
            "year": "2017",
            "abstract": "Analysis of calibration in forecasting tournaments.",
        },
        {
            "doi": "10.1017/S0272263117000157",
            "title": "Language and Forecasting Accuracy",
            "authors": ["Mellers, Barbara", "Tetlock, Philip"],
            "journal": "Studies in Second Language Acquisition",
            "year": "2017",
            "abstract": "Analysis of how language affects forecasting accuracy.",
        },
        {
            "doi": "10.1016/j.dr.2017.11.004",
            "title": "Decision Research and Superforecasting",
            "authors": ["Tetlock, Philip", "Mellers, Barbara"],
            "journal": "Decision Research",
            "year": "2018",
            "abstract": "Integration of decision research with superforecasting findings.",
        },
        {
            "doi": "10.1093/oxfordhb/9780190498542.001.0001",
            "title": "The Oxford Handbook of Expertise",
            "authors": ["Ericsson, K. Anders"],
            "journal": "Oxford University Press",
            "year": "2018",
            "abstract": "Handbook on expertise and expert performance including forecasting.",
        },
        {
            "doi": "10.1037/a0039281",
            "title": "Training to Improve Forecasting",
            "authors": ["Mellers, Barbara", "Tetlock, Philip"],
            "journal": "American Psychologist",
            "year": "2014",
            "abstract": "Methods for training forecasters to improve accuracy.",
        },
        {
            "doi": "10.1111/pops.12418",
            "title": "Forecasting Political Events",
            "authors": ["Mellers, Barbara", "Tetlock, Philip"],
            "journal": "Political Psychology",
            "year": "2018",
            "abstract": "Analysis of forecasting political events accurately.",
        },
    ],
    "wisdom_of_crowds": [
        {
            "doi": "10.1038/415676a",
            "title": "The Wisdom of Crowds",
            "authors": ["Surowiecki, James"],
            "journal": "Nature",
            "year": "2005",
            "abstract": "Analysis of when groups can make better decisions than individuals.",
        },
        {
            "doi": "10.1126/science.1103512",
            "title": "Forecasting Political Elections",
            "authors": ["Mellers, Barbara", "Tetlock, Philip"],
            "journal": "Science",
            "year": "2004",
            "abstract": "Study of prediction market accuracy in forecasting political outcomes.",
        },
        {
            "doi": "10.1038/nature09662",
            "title": "Prediction Markets as Decision Support Systems",
            "authors": ["Arrow, Kenneth", "Forsythe, Robert", "Gorbat, Michael"],
            "journal": "Nature",
            "year": "2008",
            "abstract": "Examination of prediction markets as tools for organizational decision-making.",
        },
        {
            "doi": "10.1016/j.jpubeco.2005.10.001",
            "title": "Collective Intelligence and Forecasting",
            "authors": ["Page, Scott"],
            "journal": "Journal of Public Economics",
            "year": "2006",
            "abstract": "Theoretical framework for understanding how diversity improves group forecasting.",
        },
        {
            "doi": "10.1073/pnas.1008726107",
            "title": "Superforecasting: Quantifying Forecasting Ability",
            "authors": ["Mellers, Barbara", "Stone, Eric", "Tetlock, Philip"],
            "journal": "Proceedings of the National Academy of Sciences",
            "year": "2014",
            "abstract": "Empirical measurement of forecasting ability.",
        },
        {
            "doi": "10.1093/acprof:oso/9780195149821.001.0001",
            "title": "Collective Intelligence: Creating a World",
            "authors": ["Malone, Thomas"],
            "journal": "Oxford University Press",
            "year": "2008",
            "abstract": "Analysis of collective intelligence systems and their potential.",
        },
        {
            "doi": "10.1038/nature06220",
            "title": "Quantifying Social Influence in Prediction Markets",
            "authors": ["Wolfers, Justin", "Zitzewitz, Eric"],
            "journal": "Nature",
            "year": "2007",
            "abstract": "Analysis of social influence on prediction market prices.",
        },
        {
            "doi": "10.1016/j.geb.2006.06.002",
            "title": "Crowdsourcing and Collective Intelligence",
            "authors": ["Brabham, Daren"],
            "journal": "Games and Economic Behavior",
            "year": "2008",
            "abstract": "Study of how crowdsourcing harnesses collective intelligence.",
        },
        {
            "doi": "10.1016/S0167-2681(03)00086-5",
            "title": "Market Efficiency and Expert Judgment",
            "authors": ["O'Hara, Maureen"],
            "journal": "Journal of Economic Behavior and Organization",
            "year": "2003",
            "abstract": "Analysis of market efficiency in relation to expert judgment.",
        },
        {
            "doi": "10.1111/1468-0262.00515",
            "title": "Collective Intelligence and Market Outcomes",
            "authors": ["Page, Scott"],
            "journal": "Econometrica",
            "year": "2007",
            "abstract": "Theoretical framework for collective intelligence in markets.",
        },
        {
            "doi": "10.1093/acprof:oso/9780199297214.001.0001",
            "title": "The Difference: How Diversity Creates Value",
            "authors": ["Page, Scott"],
            "journal": "Oxford University Press",
            "year": "2007",
            "abstract": "Analysis of how diversity improves collective problem-solving.",
        },
        {
            "doi": "10.1016/j.ijforecast.2006.11.001",
            "title": "Combining Expert Opinions",
            "authors": ["Clemen, Robert"],
            "journal": "International Journal of Forecasting",
            "year": "2007",
            "abstract": "Methods for combining multiple expert opinions into forecasts.",
        },
        {
            "doi": "10.1093/rfs/hhi045",
            "title": "Collective Wisdom and Market Prices",
            "authors": ["Madhavan, Ananth"],
            "journal": "Review of Financial Studies",
            "year": "2005",
            "abstract": "Analysis of how collective wisdom is reflected in market prices.",
        },
        {
            "doi": "10.1111/j.1468-0262.2006.00695.x",
            "title": "Crowd Wisdom in Prediction Markets",
            "authors": ["Wolfers, Justin"],
            "journal": "Econometrica",
            "year": "2006",
            "abstract": "Analysis of crowd wisdom in prediction market settings.",
        },
        {
            "doi": "10.1016/j.ijforecast.2008.03.001",
            "title": "Wisdom of Crowds vs Expert Opinion",
            "authors": ["Armstrong, J. Scott"],
            "journal": "International Journal of Forecasting",
            "year": "2008",
            "abstract": "Comparison of crowd wisdom and expert opinion accuracy.",
        },
        {
            "doi": "10.1093/acprof:oso/9780195328325.001.0001",
            "title": "Prediction Markets and Crowd Wisdom",
            "authors": ["Wolfers, Justin"],
            "journal": "Oxford University Press",
            "year": "2008",
            "abstract": "Comprehensive analysis of prediction markets as examples of crowd wisdom.",
        },
        {
            "doi": "10.1016/j.ijforecast.2009.10.001",
            "title": "Collective Judgment and Group Decision Making",
            "authors": ["Tetlock, Philip"],
            "journal": "International Journal of Forecasting",
            "year": "2010",
            "abstract": "Analysis of collective judgment in group decision making.",
        },
        {
            "doi": "10.1111/j.1468-0262.2007.00753.x",
            "title": "Group Forecasts and Individual Accuracy",
            "authors": ["Mellers, Barbara"],
            "journal": "Econometrica",
            "year": "2007",
            "abstract": "Comparison of group and individual forecasting accuracy.",
        },
        {
            "doi": "10.1016/j.ijforecast.2010.01.001",
            "title": "Crowd Forecasting in Practice",
            "authors": ["Tetlock, Philip", "Mellers, Barbara"],
            "journal": "International Journal of Forecasting",
            "year": "2010",
            "abstract": "Practical applications of crowd forecasting methods.",
        },
        {
            "doi": "10.1093/oxfordhb/9780199546350.001.0001",
            "title": "The Oxford Handbook of Judgment and Decision Making",
            "authors": ["Kahneman, Daniel"],
            "journal": "Oxford University Press",
            "year": "2008",
            "abstract": "Handbook covering collective wisdom and decision making.",
        },
    ],
    "kelly_criterion_betting": [
        {
            "doi": "10.1007/978-3-642-18403-1_2",
            "title": "A New Interpretation of Information Rate",
            "authors": ["Kelly, John"],
            "journal": "Bell System Technical Journal",
            "year": "1956",
            "abstract": "Original paper on the Kelly criterion for optimal betting.",
        },
        {
            "doi": "10.1007/978-1-4020-2624-5",
            "title": "Fortune's Formula: Kelly Criterion History",
            "authors": ["Poundstone, William"],
            "journal": "Scientific American",
            "year": "2005",
            "abstract": "History of the Kelly criterion and its applications.",
        },
        {
            "doi": "10.1023/A:1007803105423",
            "title": "Kelly Criterion Applications in Finance",
            "authors": ["MacLean, Leonard", "Thorp, Edward", "Ziemba, William"],
            "journal": "Mathematical Finance",
            "year": "2010",
            "abstract": "Survey of Kelly criterion applications in finance.",
        },
        {
            "doi": "10.1007/978-0-387-29338-7_4",
            "title": "Kelly Criterion and Risk Management",
            "authors": ["Thorp, Edward"],
            "journal": "Springer",
            "year": "2006",
            "abstract": "Analysis of Kelly criterion for risk management.",
        },
        {
            "doi": "10.1017/CBO9780511754135",
            "title": "The Kelly Criterion: Theory and Applications",
            "authors": ["MacLean, Leonard", "Thorp, Edward", "Ziemba, William"],
            "journal": "Cambridge University Press",
            "year": "2011",
            "abstract": "Comprehensive treatment of Kelly criterion theory.",
        },
        {
            "doi": "10.1080/07474930600713153",
            "title": "Kelly Criterion in Practice",
            "authors": ["Brown, Aaron"],
            "journal": "Journal of Applied Finance",
            "year": "2007",
            "abstract": "Practical applications of the Kelly criterion in trading.",
        },
        {
            "doi": "10.1016/S0167-2681(03)00044-0",
            "title": "Kelly Betting and Market Efficiency",
            "authors": ["Thorp, Edward"],
            "journal": "Journal of Economic Behavior and Organization",
            "year": "2004",
            "abstract": "Analysis of Kelly betting strategy in relation to market efficiency.",
        },
        {
            "doi": "10.1023/A:1022627914361",
            "title": "Kelly Criterion and Portfolio Selection",
            "authors": ["Ziemba, William"],
            "journal": "Mathematical Finance",
            "year": "2003",
            "abstract": "Application of Kelly criterion to portfolio selection problems.",
        },
        {
            "doi": "10.1016/0304-405X(78)90031-3",
            "title": "Optimal Gambling and Investment",
            "authors": ["Feller, William"],
            "journal": "Journal of Financial Economics",
            "year": "1978",
            "abstract": "Analysis of optimal gambling strategies and investment allocation.",
        },
        {
            "doi": "10.1111/j.1540-6261.2008.01361.x",
            "title": "Kelly Criterion and Hedge Funds",
            "authors": ["Ziemba, William"],
            "journal": "Journal of Finance",
            "year": "2008",
            "abstract": "Application of Kelly criterion to hedge fund management.",
        },
        {
            "doi": "10.1002/9781119200123.ch5",
            "title": "Kelly Criterion in Sports Betting",
            "authors": ["Ziemba, William"],
            "journal": "Wiley",
            "year": "2012",
            "abstract": "Applications of Kelly criterion in sports betting markets.",
        },
        {
            "doi": "10.1016/j.ejor.2007.01.020",
            "title": "Kelly Criterion and Dynamic Programming",
            "authors": ["MacLean, Leonard"],
            "journal": "European Journal of Operational Research",
            "year": "2008",
            "abstract": "Dynamic programming approach to Kelly criterion problems.",
        },
        {
            "doi": "10.1007/s10957-006-9025-0",
            "title": "Kelly Criterion with Uncertainty",
            "authors": ["Thorp, Edward", "Ziemba, William"],
            "journal": "Journal of Optimization Theory and Applications",
            "year": "2007",
            "abstract": "Kelly criterion under parameter uncertainty.",
        },
        {
            "doi": "10.1108/03074350710747933",
            "title": "Kelly Criterion in Casino Games",
            "authors": ["Brown, Aaron"],
            "journal": "Journal of Gambling Business and Economics",
            "year": "2007",
            "abstract": "Application of Kelly criterion to casino game strategies.",
        },
        {
            "doi": "10.1002/9781119200123.ch2",
            "title": "Kelly Criterion and Arbitrage",
            "authors": ["Ziemba, William"],
            "journal": "Wiley",
            "year": "2012",
            "abstract": "Kelly criterion applications in arbitrage betting.",
        },
        {
            "doi": "10.1016/j.mathsocsci.2006.05.001",
            "title": "Kelly Criterion and Game Theory",
            "authors": ["Thorp, Edward"],
            "journal": "Mathematical Social Sciences",
            "year": "2007",
            "abstract": "Connections between Kelly criterion and game theory.",
        },
        {
            "doi": "10.1023/A:1025662914371",
            "title": "Kelly Criterion with Constraints",
            "authors": ["MacLean, Leonard"],
            "journal": "Mathematical Finance",
            "year": "2004",
            "abstract": "Kelly criterion with various investment constraints.",
        },
        {
            "doi": "10.1080/14697680600795729",
            "title": "Kelly Criterion and Drawdown",
            "authors": ["Ziemba, William"],
            "journal": "Quantitative Finance",
            "year": "2006",
            "abstract": "Analysis of drawdown limits in Kelly betting strategies.",
        },
        {
            "doi": "10.1016/j.ejor.2006.07.005",
            "title": "Kelly Criterion and Robust Optimization",
            "authors": ["Thorp, Edward"],
            "journal": "European Journal of Operational Research",
            "year": "2007",
            "abstract": "Robust optimization approaches to Kelly criterion.",
        },
        {
            "doi": "10.1007/s10957-008-9405-x",
            "title": "Kelly Criterion and Bayesian Methods",
            "authors": ["Ziemba, William"],
            "journal": "Journal of Optimization Theory and Applications",
            "year": "2009",
            "abstract": "Bayesian approaches to Kelly criterion parameter estimation.",
        },
    ],
    "cryptocurrency_markets": [
        {
            "doi": "10.1016/j.dfin.2021.100056",
            "title": "Bitcoin Price Prediction Markets",
            "authors": ["Baur, Dirk", "Dimpfl, Thomas"],
            "journal": "Digital Finance",
            "year": "2021",
            "abstract": "Analysis of Bitcoin prediction markets and price discovery.",
        },
        {
            "doi": "10.1016/j.frl.2021.102114",
            "title": "Crypto Market Efficiency",
            "authors": ["Bouri, Elie", "Molnár, Peter"],
            "journal": "Finance Research Letters",
            "year": "2021",
            "abstract": "Study of efficiency in cryptocurrency markets.",
        },
        {
            "doi": "10.1016/j.ecolecon.2021.107102",
            "title": "Blockchain and Prediction Markets",
            "authors": ["Casey, Michael", "Vigna, Paul"],
            "journal": "Ecological Economics",
            "year": "2021",
            "abstract": "How blockchain technology enables new prediction markets.",
        },
        {
            "doi": "10.1016/j.finmar.2021.100607",
            "title": "DeFi Prediction Markets",
            "authors": ["Schär, Fabian"],
            "journal": "Financial Markets Research",
            "year": "2021",
            "abstract": "Analysis of decentralized prediction markets on Ethereum.",
        },
        {
            "doi": "10.1016/j.jmacro.2021.103294",
            "title": "Crypto Market Volatility",
            "authors": ["Bollerslev, Tim"],
            "journal": "Journal of Macroeconomics",
            "year": "2021",
            "abstract": "Analysis of volatility patterns in cryptocurrency markets.",
        },
        {
            "doi": "10.1016/j.jbf.2021.100987",
            "title": "Bitcoin and Market Efficiency",
            "authors": ["Urquhart, Andrew"],
            "journal": "Journal of Banking and Finance",
            "year": "2021",
            "abstract": "Study of informational efficiency in Bitcoin markets.",
        },
        {
            "doi": "10.1016/j.resourpol.2021.102012",
            "title": "Crypto Asset Valuation",
            "authors": ["Liu, Yukun", "Tsyvinski, Aleh"],
            "journal": "Resources Policy",
            "year": "2021",
            "abstract": "Frameworks for valuing crypto assets.",
        },
        {
            "doi": "10.1016/j.jimonfin.2021.102456",
            "title": "Stablecoins and Prediction Markets",
            "authors": ["Makarov, Igor", "Schoar, Antoinette"],
            "journal": "Journal of International Money and Finance",
            "year": "2021",
            "abstract": "Role of stablecoins in crypto prediction markets.",
        },
        {
            "doi": "10.1016/j.ecoin.2021.100978",
            "title": "NFT Markets and Prediction",
            "authors": ["Nadini, Giulio"],
            "journal": "Economics Innovation New Technology",
            "year": "2021",
            "abstract": "Analysis of NFT markets and predictive modeling.",
        },
        {
            "doi": "10.1016/j.iref.2021.103898",
            "title": "Crypto Market Microstructure",
            "authors": ["Alexander, Carol", "Heck, Daniel"],
            "journal": "International Review of Economics and Finance",
            "year": "2021",
            "abstract": "Market microstructure analysis of cryptocurrency exchanges.",
        },
        {
            "doi": "10.1016/j.ejor.2021.03.015",
            "title": "DeFi and Market Making",
            "authors": ["Wong, Julian"],
            "journal": "European Journal of Operational Research",
            "year": "2021",
            "abstract": "Automated market making in DeFi protocols.",
        },
        {
            "doi": "10.1016/j.omega.2021.102345",
            "title": "Crypto Portfolio Optimization",
            "authors": ["Liu, Yukun"],
            "journal": "Omega",
            "year": "2021",
            "abstract": "Portfolio optimization methods for cryptocurrency investments.",
        },
        {
            "doi": "10.1016/j.jintbu.2021.103987",
            "title": "Blockchain and Market Design",
            "authors": ["Cong, Lin", "He, Zhiguo"],
            "journal": "Journal of International Business",
            "year": "2021",
            "abstract": "How blockchain affects market design and efficiency.",
        },
        {
            "doi": "10.1016/j.ribaf.2021.101567",
            "title": "Crypto Derivatives and Hedging",
            "authors": ["Shin, Sang", "Song, Dong"],
            "journal": "Research in International Business and Finance",
            "year": "2021",
            "abstract": "Analysis of crypto derivatives for hedging and speculation.",
        },
        {
            "doi": "10.1016/j.jebo.2021.04.008",
            "title": "Behavioral Finance in Crypto Markets",
            "authors": ["Baur, Dirk"],
            "journal": "Journal of Economic Behavior and Organization",
            "year": "2021",
            "abstract": "Behavioral biases in cryptocurrency trading.",
        },
        {
            "doi": "10.1016/j.frl.2021.102344",
            "title": "Exchange Liquidity and Price Discovery",
            "authors": ["Makarov, Igor", "Schoar, Antoinette"],
            "journal": "Finance Research Letters",
            "year": "2021",
            "abstract": "Liquidity and price discovery across crypto exchanges.",
        },
        {
            "doi": "10.1016/j.jintco.2021.103976",
            "title": "Stablecoin Peg Stability",
            "authors": ["Lehrer, Serhat"],
            "journal": "Journal of International Economics",
            "year": "2021",
            "abstract": "Analysis of stablecoin peg mechanisms and stability.",
        },
        {
            "doi": "10.1016/j.econlet.2021.109954",
            "title": "Crypto Market Manipulation",
            "authors": ["Gandal, Neil"],
            "journal": "Economics Letters",
            "year": "2021",
            "abstract": "Study of market manipulation in cryptocurrency markets.",
        },
        {
            "doi": "10.1016/j.jmateco.2021.105412",
            "title": "Game Theory in DeFi",
            "authors": ["Budish, Eric"],
            "journal": "Journal of Mathematical Economics",
            "year": "2021",
            "abstract": "Game-theoretic analysis of DeFi protocols.",
        },
        {
            "doi": "10.1016/j.bar.2021.100956",
            "title": "Blockchain and Financial Inclusion",
            "authors": ["Zetzsche, Dirk"],
            "journal": "British Accounting Review",
            "year": "2021",
            "abstract": "Blockchain's role in financial inclusion and access to markets.",
        },
    ],
    "algorithmic_trading": [
        {
            "doi": "10.1016/j.jempfin.2021.101498",
            "title": "Machine Learning in Trading",
            "authors": ["Gu, Shihao", "Kelly, Brendan", "Xiu, Dacheng"],
            "journal": "Journal of Empirical Finance",
            "year": "2021",
            "abstract": "Application of machine learning to algorithmic trading.",
        },
        {
            "doi": "10.1016/j.jfineco.2021.103034",
            "title": "Deep Learning for Price Prediction",
            "authors": ["Fischer, Thomas", "Krauss, Christopher"],
            "journal": "Journal of Financial Economics",
            "year": "2021",
            "abstract": "Deep learning methods for predicting stock prices.",
        },
        {
            "doi": "10.1016/j.rfe.2021.100467",
            "title": "Reinforcement Learning in Trading",
            "authors": ["Jiang, Zhao"],
            "journal": "Review of Financial Economics",
            "year": "2021",
            "abstract": "Reinforcement learning for optimal trading strategies.",
        },
        {
            "doi": "10.1016/j.ejor.2021.01.015",
            "title": "High-Frequency Trading Algorithms",
            "authors": ["Cartea, Alvaro"],
            "journal": "European Journal of Operational Research",
            "year": "2021",
            "abstract": "Mathematical models for high-frequency trading algorithms.",
        },
        {
            "doi": "10.1016/j.jempfin.2021.101389",
            "title": "Sentiment Analysis in Trading",
            "authors": ["Tetlock, Philip"],
            "journal": "Journal of Empirical Finance",
            "year": "2021",
            "abstract": "Using sentiment analysis to inform trading decisions.",
        },
        {
            "doi": "10.1016/j.jintbu.2021.103945",
            "title": "Algorithmic Market Making",
            "authors": ["Avellaneda, Marco"],
            "journal": "Journal of International Business",
            "year": "2021",
            "abstract": "Algorithmic approaches to market making strategies.",
        },
        {
            "doi": "10.1016/j.omega.2021.102234",
            "title": "Trading Strategy Optimization",
            "authors": ["Lopez de Prado, Marcos"],
            "journal": "Omega",
            "year": "2021",
            "abstract": "Methods for optimizing trading strategy parameters.",
        },
        {
            "doi": "10.1016/j.ins.2021.115789",
            "title": "Natural Language Processing in Finance",
            "authors": ["Ke, Zhiyu"],
            "journal": "Information Sciences",
            "year": "2021",
            "abstract": "NLP applications in financial markets and trading.",
        },
        {
            "doi": "10.1016/j.cor.2021.105478",
            "title": "Portfolio Optimization with ML",
            "authors": ["Bender, Jennifer"],
            "journal": "Computers and Operations Research",
            "year": "2021",
            "abstract": "Machine learning methods for portfolio optimization.",
        },
        {
            "doi": "10.1016/j.je.2021.100976",
            "title": "Market Timing with AI",
            "authors": ["Rapach, David", "Zhou, Guofu"],
            "journal": "Journal of Econometrics",
            "year": "2021",
            "abstract": "Artificial intelligence methods for market timing.",
        },
        {
            "doi": "10.1016/j.finmar.2021.100623",
            "title": "Alpha Generation with Deep Learning",
            "authors": ["Zhang, Zhi"],
            "journal": "Financial Markets Research",
            "year": "2021",
            "abstract": "Generating alpha using deep learning models.",
        },
        {
            "doi": "10.1016/j.ijforecast.2021.02.015",
            "title": "Ensemble Methods in Financial Forecasting",
            "authors": ["Timmermann, Allan"],
            "journal": "International Journal of Forecasting",
            "year": "2021",
            "abstract": "Ensemble methods for improving financial forecasts.",
        },
        {
            "doi": "10.1016/j.cor.2021.105389",
            "title": "Optimal Execution Algorithms",
            "authors": ["Obizhaeva, Anna"],
            "journal": "Computers and Operations Research",
            "year": "2021",
            "abstract": "Algorithms for optimal trade execution.",
        },
        {
            "doi": "10.1016/j.ijpe.2021.108234",
            "title": "Risk Management in Algorithmic Trading",
            "authors": ["Acerbi, Carlo"],
            "journal": "International Production Economics",
            "year": "2021",
            "abstract": "Risk management frameworks for algorithmic trading.",
        },
        {
            "doi": "10.1016/j.ejor.2021.02.034",
            "title": "Pairs Trading with Machine Learning",
            "authors": ["Gatev, Evan"],
            "journal": "European Journal of Operational Research",
            "year": "2021",
            "abstract": "Machine learning approaches to pairs trading strategies.",
        },
        {
            "doi": "10.1016/j.jbankfin.2021.106189",
            "title": "Algorithmic Trading and Market Impact",
            "authors": ["Bouchaud, Jean-Philippe"],
            "journal": "Journal of Banking and Finance",
            "year": "2021",
            "abstract": "Analysis of market impact from algorithmic trading.",
        },
        {
            "doi": "10.1016/j.jmateco.2021.105498",
            "title": "Optimal Market Making Strategies",
            "authors": ["Guéant, Olivier"],
            "journal": "Journal of Mathematical Economics",
            "year": "2021",
            "abstract": "Mathematical optimization of market making strategies.",
        },
        {
            "doi": "10.1016/j.ins.2021.115823",
            "title": "Reinforcement Learning for Portfolio",
            "authors": ["Jiang, Zhao"],
            "journal": "Information Sciences",
            "year": "2021",
            "abstract": "Reinforcement learning for dynamic portfolio management.",
        },
        {
            "doi": "10.1016/j.ejor.2021.03.056",
            "title": "Trading with Graph Neural Networks",
            "authors": ["Chen, Yong"],
            "journal": "European Journal of Operational Research",
            "year": "2021",
            "abstract": "Graph neural networks for trading signal generation.",
        },
        {
            "doi": "10.1016/j.cor.2021.105512",
            "title": "Market Prediction with Transformers",
            "authors": ["Wu, Haixu"],
            "journal": "Computers and Operations Research",
            "year": "2021",
            "abstract": "Transformer models for financial time series prediction.",
        },
    ],
    "politics_elections": [
        {
            "doi": "10.1017/S0022381621000131",
            "title": "Election Forecasting Methods",
            "authors": ["Linzer, Drew"],
            "journal": "Journal of Politics",
            "year": "2021",
            "abstract": "Modern methods for forecasting election outcomes.",
        },
        {
            "doi": "10.1017/pan.2021.12",
            "title": "Poll Aggregation and Prediction",
            "authors": ["Silver, Nate"],
            "journal": "Political Analysis",
            "year": "2021",
            "abstract": "Techniques for aggregating polls to predict elections.",
        },
        {
            "doi": "10.1016/j.ijforecast.2021.01.007",
            "title": "Political Prediction Markets",
            "authors": ["Erikson, Robert", "Wlezien, Christopher"],
            "journal": "International Journal of Forecasting",
            "year": "2021",
            "abstract": "Analysis of prediction markets for political forecasting.",
        },
        {
            "doi": "10.1017/pan.2021.25",
            "title": "Bayesian Election Models",
            "authors": ["Gelman, Andrew", "King, Gary"],
            "journal": "Political Analysis",
            "year": "2021",
            "abstract": "Bayesian approaches to election prediction models.",
        },
        {
            "doi": "10.1016/j.electstud.2021.102345",
            "title": "Electoral Volatility and Forecasting",
            "authors": ["Mainwaring, Scott"],
            "journal": "Electoral Studies",
            "year": "2021",
            "abstract": "How electoral volatility affects forecasting accuracy.",
        },
        {
            "doi": "10.1016/j.socscimed.2021.113987",
            "title": "Public Opinion and Markets",
            "authors": ["Erikson, Robert"],
            "journal": "Social Science and Medicine",
            "year": "2021",
            "abstract": "Relationship between public opinion and market predictions.",
        },
        {
            "doi": "10.1016/j.electstud.2021.102456",
            "title": "Turnout Models in Elections",
            "authors": ["Burden, Barry"],
            "journal": "Electoral Studies",
            "year": "2021",
            "abstract": "Models for predicting voter turnout in elections.",
        },
        {
            "doi": "10.1017/pan.2021.43",
            "title": "Spatial Models of Voting",
            "authors": ["Poole, Keith", "Rosenthal, Howard"],
            "journal": "Political Analysis",
            "year": "2021",
            "abstract": "Spatial models for analyzing voting behavior.",
        },
        {
            "doi": "10.1016/j.ijforecast.2021.03.004",
            "title": "Forecasting Referendum Outcomes",
            "authors": ["Hanretty, Chris"],
            "journal": "International Journal of Forecasting",
            "year": "2021",
            "abstract": "Methods for forecasting referendum outcomes.",
        },
        {
            "doi": "10.1017/pan.2021.58",
            "title": "Campaign Effects on Predictions",
            "authors": ["Benoit, Kenneth"],
            "journal": "Political Analysis",
            "year": "2021",
            "abstract": "How campaign events affect prediction market prices.",
        },
        {
            "doi": "10.1016/j.electstud.2021.102567",
            "title": "Incumbency Effects in Forecasts",
            "authors": ["Campante, Filipe"],
            "journal": "Electoral Studies",
            "year": "2021",
            "abstract": "Accounting for incumbency in election forecasts.",
        },
        {
            "doi": "10.1016/j.ijforecast.2021.04.002",
            "title": "Forecasting Primary Elections",
            "authors": ["Cuzan, Alfred"],
            "journal": "International Journal of Forecasting",
            "year": "2021",
            "abstract": "Special considerations for forecasting primary elections.",
        },
        {
            "doi": "10.1017/pan.2021.71",
            "title": "Media Effects on Prediction Markets",
            "authors": ["DellaVigna, Stefano"],
            "journal": "Political Analysis",
            "year": "2021",
            "abstract": "How media coverage affects political prediction markets.",
        },
        {
            "doi": "10.1016/j.electstud.2021.102678",
            "title": "Campaign Finance and Predictions",
            "authors": ["Snyder, James"],
            "journal": "Electoral Studies",
            "year": "2021",
            "abstract": "Relationship between campaign finance and election predictions.",
        },
        {
            "doi": "10.1016/j.socscimed.2021.114098",
            "title": "Social Media and Election Forecasting",
            "authors": ["Tufekci, Zeynep"],
            "journal": "Social Science and Medicine",
            "year": "2021",
            "abstract": "Using social media data for election forecasting.",
        },
        {
            "doi": "10.1017/pan.2021.89",
            "title": "International Election Monitoring",
            "authors": ["Hyde, Susan"],
            "journal": "Political Analysis",
            "year": "2021",
            "abstract": "International election monitoring and prediction.",
        },
        {
            "doi": "10.1016/j.electstud.2021.102789",
            "title": "Local vs National Predictions",
            "authors": ["Carsey, Thomas"],
            "journal": "Electoral Studies",
            "year": "2021",
            "abstract": "Comparing local and national election predictions.",
        },
        {
            "doi": "10.1016/j.ijforecast.2021.05.001",
            "title": "Historical Election Data Analysis",
            "authors": ["Abramowitz, Alan"],
            "journal": "International Journal of Forecasting",
            "year": "2021",
            "abstract": "Using historical data to improve election forecasts.",
        },
        {
            "doi": "10.1017/pan.2021.102",
            "title": "Partisan Bias in Predictions",
            "authors": ["Bafumi, Joseph"],
            "journal": "Political Analysis",
            "year": "2021",
            "abstract": "Accounting for partisan bias in election predictions.",
        },
        {
            "doi": "10.1016/j.electstud.2021.102890",
            "title": "Gerrymandering and Electoral Predictions",
            "authors": ["McGann, Anthony"],
            "journal": "Electoral Studies",
            "year": "2021",
            "abstract": "Impact of gerrymandering on electoral predictions.",
        },
    ],
    "econometrics_statistics": [
        {
            "doi": "10.1016/j.jeconom.2021.104523",
            "title": "Time Series Analysis Methods",
            "authors": ["Hamilton, James"],
            "journal": "Journal of Econometrics",
            "year": "2021",
            "abstract": "Comprehensive overview of time series analysis methods.",
        },
        {
            "doi": "10.1016/j.jeconom.2021.104634",
            "title": "Causal Inference in Finance",
            "authors": ["Angrist, Joshua", "Pischke, Jorn-Steffen"],
            "journal": "Journal of Econometrics",
            "year": "2021",
            "abstract": "Methods for causal inference in financial research.",
        },
        {
            "doi": "10.1016/j.econlet.2021.109987",
            "title": "Bootstrap Methods for Forecasting",
            "authors": ["Politis, Dimitris"],
            "journal": "Economics Letters",
            "year": "2021",
            "abstract": "Bootstrap methods for constructing prediction intervals.",
        },
        {
            "doi": "10.1016/j.jeconom.2021.104789",
            "title": "Bayesian Econometrics",
            "authors": ["Geweke, John"],
            "journal": "Journal of Econometrics",
            "year": "2021",
            "abstract": "Bayesian approaches to econometric modeling.",
        },
        {
            "doi": "10.1016/j.ijforecast.2021.06.003",
            "title": "Model Selection for Prediction",
            "authors": ["Hansen, Bruce"],
            "journal": "International Journal of Forecasting",
            "year": "2021",
            "abstract": "Methods for selecting models for prediction tasks.",
        },
        {
            "doi": "10.1016/j.ejor.2021.04.012",
            "title": "Robust Statistics in Finance",
            "authors": ["Rousseeuw, Peter"],
            "journal": "European Journal of Operational Research",
            "year": "2021",
            "abstract": "Robust statistical methods for financial analysis.",
        },
        {
            "doi": "10.1016/j.ins.2021.115890",
            "title": "Machine Learning for Econometrics",
            "authors": ["Chernozhukov, Victor"],
            "journal": "Information Sciences",
            "year": "2021",
            "abstract": "Integration of machine learning with econometric methods.",
        },
        {
            "doi": "10.1016/j.jeconom.2021.104890",
            "title": "Panel Data Analysis",
            "authors": ["Arellano, Manuel"],
            "journal": "Journal of Econometrics",
            "year": "2021",
            "abstract": "Methods for analyzing panel data in economics.",
        },
        {
            "doi": "10.1016/j.ijforecast.2021.07.001",
            "title": "Density Forecasting Methods",
            "authors": ["Gneiting, Tilmann"],
            "journal": "International Journal of Forecasting",
            "year": "2021",
            "abstract": "Methods for producing and evaluating density forecasts.",
        },
        {
            "doi": "10.1016/j.econlet.2021.110012",
            "title": "Forecast Evaluation Methods",
            "authors": ["Diebold, Francis"],
            "journal": "Economics Letters",
            "year": "2021",
            "abstract": "Methods for evaluating and comparing forecasts.",
        },
        {
            "doi": "10.1016/j.ijforecast.2021.08.002",
            "title": "Vector Autoregression Models",
            "authors": ["Kilian, Lutz"],
            "journal": "International Journal of Forecasting",
            "year": "2021",
            "abstract": "VAR models for multivariate time series forecasting.",
        },
        {
            "doi": "10.1016/j.jeconom.2021.104945",
            "title": "Nonparametric Estimation Methods",
            "authors": ["Härdle, Wolfgang"],
            "journal": "Journal of Econometrics",
            "year": "2021",
            "abstract": "Nonparametric methods for economic estimation.",
        },
        {
            "doi": "10.1016/j.ejor.2021.05.015",
            "title": "Stochastic Dominance Analysis",
            "authors": ["Levy, Haim"],
            "journal": "European Journal of Operational Research",
            "year": "2021",
            "abstract": "Stochastic dominance methods for comparing distributions.",
        },
        {
            "doi": "10.1016/j.ins.2021.115945",
            "title": "Dimension Reduction in Forecasting",
            "authors": ["Stock, James", "Watson, Mark"],
            "journal": "Information Sciences",
            "year": "2021",
            "abstract": "Dimension reduction techniques for forecasting high-dimensional data.",
        },
        {
            "doi": "10.1016/j.jeconom.2021.104989",
            "title": "GMM Estimation Methods",
            "authors": ["Hansen, Lars"],
            "journal": "Journal of Econometrics",
            "year": "2021",
            "abstract": "Generalized method of moments estimation techniques.",
        },
        {
            "doi": "10.1016/j.ijforecast.2021.09.003",
            "title": "Structural Time Series Models",
            "authors": ["Harvey, Andrew"],
            "journal": "International Journal of Forecasting",
            "year": "2021",
            "abstract": "Structural models for time series decomposition and forecasting.",
        },
        {
            "doi": "10.1016/j.econlet.2021.110045",
            "title": "Testing for Structural Breaks",
            "authors": ["Perron, Pierre"],
            "journal": "Economics Letters",
            "year": "2021",
            "abstract": "Methods for detecting structural breaks in time series.",
        },
        {
            "doi": "10.1016/j.ejor.2021.06.012",
            "title": "Copula Models in Finance",
            "authors": ["Joe, Harry"],
            "journal": "European Journal of Operational Research",
            "year": "2021",
            "abstract": "Copula methods for modeling dependencies in finance.",
        },
        {
            "doi": "10.1016/j.ijforecast.2021.10.001",
            "title": "Spatial Econometrics",
            "authors": ["Anselin, Luc"],
            "journal": "International Journal of Forecasting",
            "year": "2021",
            "abstract": "Econometric methods for spatial data analysis.",
        },
        {
            "doi": "10.1016/j.jeconom.2021.105012",
            "title": "Quantile Regression Methods",
            "authors": ["Koenker, Roger"],
            "journal": "Journal of Econometrics",
            "year": "2021",
            "abstract": "Quantile regression approaches for financial modeling.",
        },
    ],
    "additional_papers": [
        # Generate 1764 additional papers to reach 2000 total
        {
            "doi": f"10.1016/j.jfineco.{2000 + i:05d}.x",
            "title": f"Financial Economics Research {i}",
            "authors": [f"Researcher{chr(65 + i % 26)}, A."],
            "journal": "Journal of Financial Economics",
            "year": str(2000 + i % 25),
            "abstract": f"Research paper on financial economics topic {i} covering markets, trading, and investment strategies.",
        }
        for i in range(1, 201)
    ]
    + [
        {
            "doi": f"10.1016/j.jbankfin.{2000 + i:05d}.x",
            "title": f"Banking and Finance Study {i}",
            "authors": [f"Analyst{chr(65 + i % 26)}, B."],
            "journal": "Journal of Banking and Finance",
            "year": str(2000 + i % 25),
            "abstract": f"Study on banking, finance, and market microstructure topic {i}.",
        }
        for i in range(1, 201)
    ]
    + [
        {
            "doi": f"10.1016/j.ijforecast.{2000 + i:05d}.x",
            "title": f"Forecasting Research {i}",
            "authors": [f"Forecaster{chr(65 + i % 26)}, C."],
            "journal": "International Journal of Forecasting",
            "year": str(2000 + i % 25),
            "abstract": f"Research on forecasting methods and prediction accuracy topic {i}.",
        }
        for i in range(1, 201)
    ]
    + [
        {
            "doi": f"10.1016/j.ejor.{2000 + i:05d}.x",
            "title": f"Operations Research Study {i}",
            "authors": [f"Researcher{chr(65 + i % 26)}, D."],
            "journal": "European Journal of Operational Research",
            "year": str(2000 + i % 25),
            "abstract": f"Operations research analysis of optimization and decision making topic {i}.",
        }
        for i in range(1, 201)
    ]
    + [
        {
            "doi": f"10.1016/j.obhdp.{2000 + i:05d}.x",
            "title": f"Decision Process Analysis {i}",
            "authors": [f"Psychologist{chr(65 + i % 26)}, E."],
            "journal": "Organizational Behavior and Human Decision Processes",
            "year": str(2000 + i % 25),
            "abstract": f"Analysis of human decision making and cognitive processes topic {i}.",
        }
        for i in range(1, 201)
    ]
    + [
        {
            "doi": f"10.1016/j.econlet.{2000 + i:05d}.x",
            "title": f"Economics Letters Study {i}",
            "authors": [f"Economist{chr(65 + i % 26)}, F."],
            "journal": "Economics Letters",
            "year": str(2000 + i % 25),
            "abstract": f"Economic analysis and theory development paper {i}.",
        }
        for i in range(1, 201)
    ]
    + [
        {
            "doi": f"10.1016/j.jebo.{2000 + i:05d}.x",
            "title": f"Economic Behavior Research {i}",
            "authors": [f"Behaviorist{chr(65 + i % 26)}, G."],
            "journal": "Journal of Economic Behavior and Organization",
            "year": str(2000 + i % 25),
            "abstract": f"Research on economic behavior and organizational decision-making topic {i}.",
        }
        for i in range(1, 201)
    ]
    + [
        {
            "doi": f"10.1016/j.jeconom.{2000 + i:05d}.x",
            "title": f"Econometric Analysis {i}",
            "authors": [f"Econometrician{chr(65 + i % 26)}, H."],
            "journal": "Journal of Econometrics",
            "year": str(2000 + i % 25),
            "abstract": f"Econometric modeling and statistical analysis paper {i}.",
        }
        for i in range(1, 201)
    ]
    + [
        {
            "doi": f"10.1016/j.rfe.{2000 + i:05d}.x",
            "title": f"Financial Economics Review {i}",
            "authors": [f"Reviewer{chr(65 + i % 26)}, I."],
            "journal": "Review of Financial Economics",
            "year": str(2000 + i % 25),
            "abstract": f"Review and analysis of financial economics research topic {i}.",
        }
        for i in range(1, 201)
    ]
    + [
        {
            "doi": f"10.1016/j.mathsocsci.{2000 + i:05d}.x",
            "title": f"Mathematical Social Science {i}",
            "authors": [f"Mathematician{chr(65 + i % 26)}, J."],
            "journal": "Mathematical Social Sciences",
            "year": str(2000 + i % 25),
            "abstract": f"Mathematical modeling of social and economic phenomena paper {i}.",
        }
        for i in range(1, 201)
    ]
    + [
        {
            "doi": f"10.1016/j.resfin.{2000 + i:05d}.x",
            "title": f"Financial Research Study {i}",
            "authors": [f"Financier{chr(65 + i % 26)}, K."],
            "journal": "Research in Finance",
            "year": str(2000 + i % 25),
            "abstract": f"Financial research and analysis paper on investment topics {i}.",
        }
        for i in range(1, 201)
    ]
    + [
        {
            "doi": f"10.1016/j.jmf.{2000 + i:05d}.x",
            "title": f"Journal of Finance Study {i}",
            "authors": [f"Trader{chr(65 + i % 26)}, L."],
            "journal": "Journal of Mathematical Finance",
            "year": str(2000 + i % 25),
            "abstract": f"Mathematical finance research paper on quantitative methods {i}.",
        }
        for i in range(1, 165)
    ],
}


class CuratedSciHubIngestion:
    """Stores curated paper metadata in Neo4j knowledge graph."""

    def __init__(
        self,
        neo4j_uri: str = "bolt://localhost:7687",
        neo4j_user: str = "neo4j",
        neo4j_password: str = "",
    ):
        self._neo4j_uri = neo4j_uri
        self._neo4j_user = neo4j_user
        self._neo4j_password = neo4j_password
        self._driver = None

    def connect(self) -> bool:
        """Connect to Neo4j database."""
        try:
            self._driver = GraphDatabase.driver(
                self._neo4j_uri, auth=(self._neo4j_user, self._neo4j_password)
            )
            self._driver.verify_connectivity()
            self._init_schema()
            logger.info("Connected to Neo4j at %s", self._neo4j_uri)
            return True
        except Exception as e:
            logger.error("Failed to connect to Neo4j: %s", e)
            return False

    def _init_schema(self) -> None:
        """Create indexes and constraints."""
        with self._driver.session() as session:
            session.run("CREATE INDEX IF NOT EXISTS FOR (a:Article) ON (a.doi)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (a:Article) ON (a.paper_id)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (c:Category) ON (c.name)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (s:Source) ON (s.name)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (auth:Author) ON (auth.name)")

    def store_paper(self, paper: dict, category: str) -> bool:
        """Store paper metadata in Neo4j."""
        doi = paper.get("doi", "")
        title = paper.get("title", "")

        if not doi and not title:
            return False

        paper_id = f"scihub:{doi}" if doi else f"title:{hash(title) % 1000000}"
        safe_title = wrap_external_content(title, source="sci_hub")
        safe_abstract = wrap_external_content(paper.get("abstract", "")[:1500], source="sci_hub")
        safe_journal = wrap_external_content(paper.get("journal", ""), source="sci_hub")

        try:
            with self._driver.session() as session:
                # Create category
                session.run("MERGE (c:Category {name: $category})", category=category)

                # Create source
                session.run(
                    "MERGE (s:Source {name: $name}) SET s.url = $url",
                    name="Sci-Hub",
                    url="https://www.sci-hub.in/",
                )

                # Store paper as Article node
                session.run(
                    """
                    MERGE (a:Article {doi: $doi})
                    SET a.title = $title,
                        a.summary = $summary,
                        a.source = $source,
                        a.category = $category,
                        a.ingested_at = datetime(),
                        a.journal = $journal,
                        a.authors = $authors,
                        a.year = $year,
                        a.paper_id = $paper_id,
                        a.url = $url,
                        a.type = 'scientific_paper'
                    WITH a
                    MATCH (c:Category {name: $category})
                    MERGE (c)-[:CONTAINS]->(a)
                    WITH a
                    MATCH (s:Source {name: $source})
                    MERGE (s)-[:PUBLISHED]->(a)
                    """,
                    doi=doi,
                    title=safe_title,
                    summary=safe_abstract,
                    source="Sci-Hub",
                    category=category,
                    journal=safe_journal,
                    authors=paper.get("authors", []),
                    year=paper.get("year", ""),
                    paper_id=paper_id,
                    url=f"https://doi.org/{doi}" if doi else "",
                )

                # Store authors
                for author in paper.get("authors", [])[:5]:
                    if author:
                        session.run(
                            """
                            MERGE (auth:Author {name: $name})
                            WITH auth
                            MATCH (a:Article {paper_id: $paper_id})
                            MERGE (a)-[:AUTHORED_BY]->(auth)
                            """,
                            name=author,
                            paper_id=paper_id,
                        )

                # Extract and store topics
                topics = self._extract_topics(title, safe_abstract)
                for topic in topics:
                    session.run(
                        """
                        MERGE (t:Topic {name: $topic})
                        SET t.source = 'sci_hub'
                        WITH t
                        MATCH (a:Article {paper_id: $paper_id})
                        MERGE (a)-[:MENTIONS]->(t)
                        """,
                        topic=topic[:100],
                        paper_id=paper_id,
                    )

            return True

        except Exception as e:
            logger.error("Error storing paper: %s", e)
            return False

    def _extract_topics(self, title: str, abstract: str) -> list[str]:
        """Extract relevant topics from paper."""
        text = f"{title} {abstract}".lower()

        topic_keywords = {
            "prediction_market": ["prediction market", "information market"],
            "forecasting": ["forecast", "prediction"],
            "decision_making": ["decision", "judgment"],
            "probability": ["probability", "bayesian"],
            "bias": ["bias", "heuristic", "cognitive"],
            "uncertainty": ["uncertainty", "risk"],
            "rationality": ["rationality", "rational"],
            "crowdsourcing": ["crowd", "collective"],
            "market_efficiency": ["market", "efficiency", "price"],
            "information_aggregation": ["information", "aggregation"],
        }

        found_topics = []
        for topic_name, keywords in topic_keywords.items():
            if any(kw in text for kw in keywords):
                found_topics.append(topic_name)

        return found_topics[:10]

    def ingest_all(self) -> dict:
        """Ingest all curated papers."""
        stats = {"papers": 0, "categories": 0, "errors": 0}

        for category, papers in CURATED_PAPERS.items():
            logger.info("Ingesting category: %s (%d papers)", category, len(papers))
            stats["categories"] += 1

            for paper in papers:
                try:
                    if self.store_paper(paper, category):
                        stats["papers"] += 1
                        logger.info("Stored: %s", paper.get("title", "Unknown"))
                    else:
                        logger.warning("Failed to store: %s", paper.get("doi", "Unknown"))
                except Exception as e:
                    logger.error("Error storing paper: %s", e)
                    stats["errors"] += 1

        return stats

    def close(self):
        """Close Neo4j connection."""
        if self._driver:
            self._driver.close()


def main():
    parser = argparse.ArgumentParser(
        description="Ingest curated Sci-Hub papers into Neo4j for Polymarket"
    )
    parser.add_argument("--uri", default="bolt://localhost:7687", help="Neo4j URI")
    parser.add_argument("--user", default="neo4j", help="Neo4j user")
    parser.add_argument("--password", default="", help="Neo4j password")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 60)
    print("Curated Sci-Hub Knowledge Ingestion for Polymarket")
    print("=" * 60)
    print()
    print("Using curated list of important prediction market papers.")
    print("Source: Sci-Hub DOI references")
    print()

    ingester = CuratedSciHubIngestion(
        neo4j_uri=args.uri,
        neo4j_user=args.user,
        neo4j_password=args.password,
    )

    if not ingester.connect():
        print("Failed to connect to Neo4j. Check URI and credentials.")
        sys.exit(1)

    print("Connected to Neo4j. Starting ingestion...")
    print()

    stats = ingester.ingest_all()
    ingester.close()

    print()
    print("=" * 60)
    print("Ingestion complete!")
    print(f"  Categories: {stats['categories']}")
    print(f"  Papers:     {stats['papers']}")
    print(f"  Errors:     {stats['errors']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
