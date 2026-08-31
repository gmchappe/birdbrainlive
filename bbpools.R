bbpools <- function(link)
{
  require(googlesheets4)
  require(tidyverse)
  df <- read_sheet(ss=link,
                   sheet='Full Season Scores')
  x <- quantile(rowMeans(df[2:ncol(df)], na.rm = TRUE), probs = c(0.2, 0.4, 0.6, 0.8), na.rm=TRUE)
  
  avgs <- df %>%
    select(-Name) %>%
    transmute(avg = rowMeans(across(everything()), na.rm=TRUE))
  
  df2 <- cbind(df,avgs)
  
  pools <- df2 %>%
    transmute(Name,
              Pool = case_when(avg <= as.numeric(x[1]) ~ 'A',
                               avg <= as.numeric(x[2]) ~ 'B',
                               avg <= as.numeric(x[3]) ~ 'C',
                               avg <= as.numeric(x[4]) ~ 'D',
                               !is.na(avg) ~ 'E',
                               TRUE ~ NA)) %>%
    filter(!is.na(Name))
  
  write_sheet(pools,
              ss=link,
              sheet='Player Pool Assignments')
  
}
