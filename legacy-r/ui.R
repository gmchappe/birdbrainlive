library(shiny)
library(ggplot2)
library(DT)

fluidPage(
  
  titlePanel("Birdbrain Disc Golf Club"),
  
  mainPanel(
    tabsetPanel(
      # tabPanel("Playoff Results", DTOutput("semis")),
      tabPanel("Standings", DTOutput("standings")),
      tabPanel("2026 Schedule", DTOutput("schedule")),
      tabPanel("Payout Calculator",
               numericInput("homies",
                            "Number of Players: ",
                            24, 
                            min = 1, 
                            step = 1),
               selectInput("scale", 
                           "Double Points?",
                           choices = c("Yes", "No"),
                           selected = "No"),
               tableOutput("paytable")),
      tabPanel("All-Time Points", DTOutput("alltime")),
      tabPanel("Course Records", DTOutput("records")),
      tabPanel("Aces", DTOutput("aces")),
      tabPanel("Results Finalizer v2.0",
               fileInput("results",
                         "Upload UDisc results:"),
               tableOutput("resultable")),
      # tabPanel("Playoff Results Finalizer",
      #          fileInput("pround1",
      #                    "Upload Round 1:"),
      #          fileInput("pround2",
      #                    "Upload Round 2:"),
      #          tableOutput("playofftable")),
      tabPanel("Hall of Champions", DTOutput("champs"))
    )
  )
)
